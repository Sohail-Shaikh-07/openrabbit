"""Tests for persistent webhook delivery state and idempotency claims."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from api import (
    DELIVERY_STATE_FILENAME,
    DeliveryStateCapacityError,
    DeliveryStateConflict,
    DeliveryStateError,
    SQLiteDeliveryStateStore,
    delivery_state_path,
)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _claim(
    store: SQLiteDeliveryStateStore,
    delivery_id: str = "delivery-1",
    **overrides: object,
):
    values = {
        "event_name": "pull_request",
        "repository": "o/r",
        "pr_number": 42,
        "operation": "pull_request_opened",
        **overrides,
    }
    return store.claim(delivery_id=delivery_id, **values)  # type: ignore[arg-type]


def test_delivery_state_path_is_workspace_local(tmp_path: Path) -> None:
    assert delivery_state_path(tmp_path) == tmp_path / ".openrabbit" / DELIVERY_STATE_FILENAME


def test_new_delivery_is_claimed_and_round_trips_bounded_metadata(tmp_path: Path) -> None:
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")

    claim = _claim(store)

    assert claim.claimed is True
    assert claim.reason == "scheduled"
    assert claim.record.status == "processing"
    assert claim.record.attempts == 1
    assert store.get("delivery-1") == claim.record
    with closing(sqlite3.connect(store.path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(webhook_deliveries)")}
    assert not columns.intersection({"payload", "secret", "prompt", "token", "exception"})


def test_processing_and_succeeded_duplicates_are_not_claimed(tmp_path: Path) -> None:
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")
    _claim(store)

    processing_duplicate = _claim(store)
    completed = store.mark_succeeded("delivery-1")
    succeeded_duplicate = _claim(store)

    assert processing_duplicate.claimed is False
    assert processing_duplicate.reason == "duplicate_processing"
    assert completed.status == "succeeded"
    assert succeeded_duplicate.claimed is False
    assert succeeded_duplicate.reason == "duplicate_succeeded"


def test_failed_delivery_can_be_retried_with_incremented_attempt(tmp_path: Path) -> None:
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")
    _claim(store)
    failed = store.mark_failed("delivery-1", error_type="RuntimeError")

    retried = _claim(store)

    assert failed.status == "failed"
    assert failed.last_error_type == "RuntimeError"
    assert retried.claimed is True
    assert retried.reason == "retry_scheduled"
    assert retried.record.status == "processing"
    assert retried.record.attempts == 2
    assert retried.record.last_error_type is None


def test_stale_processing_delivery_can_be_reclaimed(tmp_path: Path) -> None:
    clock = _Clock()
    store = SQLiteDeliveryStateStore(
        tmp_path / "deliveries.sqlite3",
        processing_timeout=timedelta(minutes=5),
        clock=clock,
    )
    _claim(store)
    clock.advance(timedelta(minutes=6))

    retried = _claim(store)

    assert retried.claimed is True
    assert retried.reason == "retry_scheduled"
    assert retried.record.attempts == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_name": "issue_comment"},
        {"repository": "other/repo"},
        {"pr_number": 43},
        {"operation": "commit_pushed"},
    ],
)
def test_conflicting_delivery_metadata_is_rejected(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")
    _claim(store)

    with pytest.raises(DeliveryStateConflict, match="conflicting"):
        _claim(store, **overrides)


def test_repository_case_does_not_create_false_conflict(tmp_path: Path) -> None:
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")
    _claim(store, repository="O/R")

    duplicate = _claim(store, repository="o/r")

    assert duplicate.reason == "duplicate_processing"


def test_ignored_delivery_is_recorded_and_deduplicated(tmp_path: Path) -> None:
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")

    first = store.record_ignored(
        delivery_id="ping-1",
        event_name="ping",
        repository="o/r",
        pr_number=None,
        operation="ping",
        reason="ping",
    )
    duplicate = store.record_ignored(
        delivery_id="ping-1",
        event_name="ping",
        repository="o/r",
        pr_number=None,
        operation="ping",
        reason="ping",
    )

    assert first.reason == "ping"
    assert first.record.status == "ignored"
    assert first.record.attempts == 0
    assert duplicate.reason == "duplicate_ignored"


def test_retention_removes_oldest_terminal_delivery(tmp_path: Path) -> None:
    clock = _Clock()
    store = SQLiteDeliveryStateStore(
        tmp_path / "deliveries.sqlite3",
        retention_limit=2,
        clock=clock,
    )
    _claim(store, "delivery-1")
    store.mark_succeeded("delivery-1")
    clock.advance(timedelta(seconds=1))
    _claim(store, "delivery-2")
    store.mark_succeeded("delivery-2")
    clock.advance(timedelta(seconds=1))

    _claim(store, "delivery-3")

    assert store.get("delivery-1") is None
    assert [record.delivery_id for record in store.list_recent()] == [
        "delivery-3",
        "delivery-2",
    ]


def test_retention_never_removes_active_delivery(tmp_path: Path) -> None:
    store = SQLiteDeliveryStateStore(
        tmp_path / "deliveries.sqlite3",
        retention_limit=1,
    )
    _claim(store, "delivery-1")

    with pytest.raises(DeliveryStateCapacityError, match="capacity"):
        _claim(store, "delivery-2")

    assert store.get("delivery-1") is not None
    assert store.get("delivery-2") is None


def test_concurrent_claims_dispatch_only_once(tmp_path: Path) -> None:
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda _: _claim(store), range(16)))

    assert sum(claim.claimed for claim in claims) == 1
    assert {claim.reason for claim in claims} == {"scheduled", "duplicate_processing"}


def test_invalid_transition_and_unknown_schema_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "deliveries.sqlite3"
    store = SQLiteDeliveryStateStore(path)
    with pytest.raises(DeliveryStateError, match="not currently processing"):
        store.mark_succeeded("missing")

    path.unlink()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(DeliveryStateError, match="schema version"):
        SQLiteDeliveryStateStore(path)
