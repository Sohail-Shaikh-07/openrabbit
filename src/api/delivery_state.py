"""Persistent webhook delivery state and idempotency claims."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

DeliveryStatus = Literal["processing", "succeeded", "failed", "ignored"]
DeliveryClaimReason = Literal[
    "scheduled",
    "retry_scheduled",
    "duplicate_processing",
    "duplicate_succeeded",
    "duplicate_ignored",
]

DELIVERY_STATE_FILENAME = "webhook-deliveries.sqlite3"
DEFAULT_RETENTION_LIMIT = 5_000
DEFAULT_PROCESSING_TIMEOUT = timedelta(minutes=15)


class DeliveryStateError(RuntimeError):
    """Base error for delivery-state persistence failures."""


class DeliveryStateConflict(DeliveryStateError):
    """Raised when one delivery ID is reused with different metadata."""


class DeliveryStateCapacityError(DeliveryStateError):
    """Raised when retention is full of deliveries still being processed."""


@dataclass(frozen=True)
class DeliveryRecord:
    """Bounded operational metadata for one GitHub webhook delivery."""

    delivery_id: str
    event_name: str
    repository: str
    pr_number: int | None
    operation: str
    status: DeliveryStatus
    attempts: int
    received_at: str
    updated_at: str
    reason: str
    last_error_type: str | None = None


@dataclass(frozen=True)
class DeliveryClaim:
    """Outcome of atomically claiming or recording a delivery."""

    claimed: bool
    reason: str
    record: DeliveryRecord


def delivery_state_path(workspace: Path) -> Path:
    """Return the workspace-local webhook delivery database path."""
    return workspace / ".openrabbit" / DELIVERY_STATE_FILENAME


class SQLiteDeliveryStateStore:
    """Concurrency-safe SQLite delivery ledger with bounded retention."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: Path,
        *,
        retention_limit: int = DEFAULT_RETENTION_LIMIT,
        processing_timeout: timedelta = DEFAULT_PROCESSING_TIMEOUT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retention_limit < 1:
            raise ValueError("retention_limit must be at least 1")
        if processing_timeout <= timedelta(0):
            raise ValueError("processing_timeout must be positive")
        self._path = path
        self._retention_limit = retention_limit
        self._processing_timeout = processing_timeout
        self._clock = clock or (lambda: datetime.now(UTC))
        try:
            self._initialize()
        except DeliveryStateError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DeliveryStateError("Webhook delivery state could not be initialized.") from exc

    @property
    def path(self) -> Path:
        return self._path

    def claim(
        self,
        *,
        delivery_id: str,
        event_name: str,
        repository: str,
        pr_number: int | None,
        operation: str,
    ) -> DeliveryClaim:
        """Atomically claim new or previously failed/stale delivery work."""
        metadata = _validated_metadata(
            delivery_id=delivery_id,
            event_name=event_name,
            repository=repository,
            pr_number=pr_number,
            operation=operation,
        )
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM webhook_deliveries WHERE delivery_id = ?",
                (metadata.delivery_id,),
            ).fetchone()
            if row is None:
                self._make_room(connection)
                connection.execute(
                    """
                    INSERT INTO webhook_deliveries (
                        delivery_id, event_name, repository, pr_number, operation,
                        status, attempts, received_at, updated_at, reason, last_error_type
                    ) VALUES (?, ?, ?, ?, ?, 'processing', 1, ?, ?, 'scheduled', NULL)
                    """,
                    (
                        metadata.delivery_id,
                        metadata.event_name,
                        metadata.repository,
                        metadata.pr_number,
                        metadata.operation,
                        now,
                        now,
                    ),
                )
                connection.commit()
                record = self.get(metadata.delivery_id)
                assert record is not None
                return DeliveryClaim(claimed=True, reason="scheduled", record=record)

            existing = _record_from_row(row)
            _raise_on_conflict(existing, metadata)
            retryable = existing.status == "failed" or (
                existing.status == "processing" and self._is_stale(existing, now)
            )
            if retryable:
                connection.execute(
                    """
                    UPDATE webhook_deliveries
                    SET status = 'processing', attempts = attempts + 1,
                        updated_at = ?, reason = 'retry_scheduled', last_error_type = NULL
                    WHERE delivery_id = ?
                    """,
                    (now, metadata.delivery_id),
                )
                connection.commit()
                record = self.get(metadata.delivery_id)
                assert record is not None
                return DeliveryClaim(claimed=True, reason="retry_scheduled", record=record)

            if existing.status == "processing":
                reason: DeliveryClaimReason = "duplicate_processing"
            elif existing.status == "succeeded":
                reason = "duplicate_succeeded"
            elif existing.status == "ignored":
                reason = "duplicate_ignored"
            else:  # The failed state is retried above.
                raise DeliveryStateError("Unexpected webhook delivery state.")
            connection.commit()
            return DeliveryClaim(claimed=False, reason=reason, record=existing)
        except sqlite3.Error as exc:
            connection.rollback()
            raise DeliveryStateError("Webhook delivery state operation failed.") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_ignored(
        self,
        *,
        delivery_id: str,
        event_name: str,
        repository: str,
        pr_number: int | None,
        operation: str,
        reason: str,
    ) -> DeliveryClaim:
        """Record an ignored signed delivery and suppress later duplicates."""
        metadata = _validated_metadata(
            delivery_id=delivery_id,
            event_name=event_name,
            repository=repository,
            pr_number=pr_number,
            operation=operation,
        )
        normalized_reason = _bounded_text(reason, field="reason", maximum=128)
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM webhook_deliveries WHERE delivery_id = ?",
                (metadata.delivery_id,),
            ).fetchone()
            if row is not None:
                existing = _record_from_row(row)
                _raise_on_conflict(existing, metadata)
                connection.commit()
                if existing.status != "ignored":
                    raise DeliveryStateConflict(
                        "Webhook delivery ID already has actionable processing state."
                    )
                return DeliveryClaim(
                    claimed=False,
                    reason="duplicate_ignored",
                    record=existing,
                )

            self._make_room(connection)
            connection.execute(
                """
                INSERT INTO webhook_deliveries (
                    delivery_id, event_name, repository, pr_number, operation,
                    status, attempts, received_at, updated_at, reason, last_error_type
                ) VALUES (?, ?, ?, ?, ?, 'ignored', 0, ?, ?, ?, NULL)
                """,
                (
                    metadata.delivery_id,
                    metadata.event_name,
                    metadata.repository,
                    metadata.pr_number,
                    metadata.operation,
                    now,
                    now,
                    normalized_reason,
                ),
            )
            connection.commit()
            record = self.get(metadata.delivery_id)
            assert record is not None
            return DeliveryClaim(claimed=False, reason=normalized_reason, record=record)
        except sqlite3.Error as exc:
            connection.rollback()
            raise DeliveryStateError("Webhook delivery state operation failed.") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_succeeded(self, delivery_id: str) -> DeliveryRecord:
        """Mark one claimed delivery as completed successfully."""
        return self._finish(
            delivery_id,
            status="succeeded",
            reason="completed",
            last_error_type=None,
        )

    def mark_failed(self, delivery_id: str, *, error_type: str) -> DeliveryRecord:
        """Mark one claimed delivery as failed using only a safe error type."""
        return self._finish(
            delivery_id,
            status="failed",
            reason="dispatch_failed",
            last_error_type=_bounded_text(
                error_type,
                field="error_type",
                maximum=128,
            ),
        )

    def get(self, delivery_id: str) -> DeliveryRecord | None:
        """Return one delivery record without exposing payload data."""
        normalized_id = _bounded_text(delivery_id, field="delivery_id", maximum=128)
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM webhook_deliveries WHERE delivery_id = ?",
                    (normalized_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise DeliveryStateError("Webhook delivery state operation failed.") from exc
        return _record_from_row(row) if row is not None else None

    def list_recent(self, *, limit: int = 100) -> list[DeliveryRecord]:
        """Return recent bounded delivery metadata for diagnostics."""
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM webhook_deliveries
                    ORDER BY updated_at DESC, delivery_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise DeliveryStateError("Webhook delivery state operation failed.") from exc
        return [_record_from_row(row) for row in rows]

    def _finish(
        self,
        delivery_id: str,
        *,
        status: Literal["succeeded", "failed"],
        reason: str,
        last_error_type: str | None,
    ) -> DeliveryRecord:
        normalized_id = _bounded_text(delivery_id, field="delivery_id", maximum=128)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = ?, updated_at = ?, reason = ?, last_error_type = ?
                WHERE delivery_id = ? AND status = 'processing'
                """,
                (status, self._now(), reason, last_error_type, normalized_id),
            )
            if result.rowcount != 1:
                raise DeliveryStateError(
                    "Webhook delivery is missing or is not currently processing."
                )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise DeliveryStateError("Webhook delivery state operation failed.") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        record = self.get(normalized_id)
        assert record is not None
        return record

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, self.SCHEMA_VERSION):
                raise DeliveryStateError("Unsupported webhook delivery state schema version.")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    pr_number INTEGER,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('processing', 'succeeded', 'failed', 'ignored')
                    ),
                    attempts INTEGER NOT NULL CHECK (attempts >= 0),
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    last_error_type TEXT
                )
                """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS webhook_deliveries_updated_idx
                ON webhook_deliveries(updated_at)
                """)
            connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self._path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection
        except sqlite3.Error as exc:
            raise DeliveryStateError("Webhook delivery state is unavailable.") from exc

    def _make_room(self, connection: sqlite3.Connection) -> None:
        count = int(connection.execute("SELECT COUNT(*) FROM webhook_deliveries").fetchone()[0])
        remove_count = count - self._retention_limit + 1
        if remove_count > 0:
            connection.execute(
                """
                DELETE FROM webhook_deliveries
                WHERE delivery_id IN (
                    SELECT delivery_id FROM webhook_deliveries
                    WHERE status != 'processing'
                    ORDER BY updated_at ASC, delivery_id ASC
                    LIMIT ?
                )
                """,
                (remove_count,),
            )
        remaining = int(connection.execute("SELECT COUNT(*) FROM webhook_deliveries").fetchone()[0])
        if remaining >= self._retention_limit:
            raise DeliveryStateCapacityError(
                "Webhook delivery state is at capacity with active deliveries."
            )

    def _is_stale(self, record: DeliveryRecord, now: str) -> bool:
        updated_at = datetime.fromisoformat(record.updated_at)
        current = datetime.fromisoformat(now)
        return updated_at <= current - self._processing_timeout

    def _now(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class _DeliveryMetadata:
    delivery_id: str
    event_name: str
    repository: str
    pr_number: int | None
    operation: str


def _validated_metadata(
    *,
    delivery_id: str,
    event_name: str,
    repository: str,
    pr_number: int | None,
    operation: str,
) -> _DeliveryMetadata:
    if pr_number is not None and (
        isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0
    ):
        raise ValueError("pr_number must be a positive integer")
    return _DeliveryMetadata(
        delivery_id=_bounded_text(delivery_id, field="delivery_id", maximum=128),
        event_name=_bounded_text(event_name, field="event_name", maximum=64).lower(),
        repository=_bounded_text(repository, field="repository", maximum=255),
        pr_number=pr_number,
        operation=_bounded_text(operation, field="operation", maximum=128),
    )


def _bounded_text(value: str, *, field: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters")
    return normalized


def _record_from_row(row: sqlite3.Row) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=str(row["delivery_id"]),
        event_name=str(row["event_name"]),
        repository=str(row["repository"]),
        pr_number=int(row["pr_number"]) if row["pr_number"] is not None else None,
        operation=str(row["operation"]),
        status=cast(DeliveryStatus, str(row["status"])),
        attempts=int(row["attempts"]),
        received_at=str(row["received_at"]),
        updated_at=str(row["updated_at"]),
        reason=str(row["reason"]),
        last_error_type=(
            str(row["last_error_type"]) if row["last_error_type"] is not None else None
        ),
    )


def _raise_on_conflict(existing: DeliveryRecord, metadata: _DeliveryMetadata) -> None:
    if (
        existing.event_name != metadata.event_name
        or existing.repository.casefold() != metadata.repository.casefold()
        or existing.pr_number != metadata.pr_number
        or existing.operation != metadata.operation
    ):
        raise DeliveryStateConflict(
            "Webhook delivery ID was reused with conflicting event metadata."
        )
