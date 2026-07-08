"""SQLite-backed local memory store for PR reviews."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.models import Finding
from memory.fingerprints import fingerprint_finding
from memory.models import (
    FindingComparison,
    FindingMemoryRecord,
    FindingStatus,
    LearningMemoryRecord,
    PullRequestMemoryHistory,
    ReviewMemoryWrite,
    finding_payload,
)


class SQLitePullRequestMemory:
    """Persist structured PR review memory in a local SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def load_history(self, repo: str, pr_number: int) -> PullRequestMemoryHistory:
        """Return stored memory for ``repo`` PR ``pr_number``."""
        with self._connect() as con:
            last_reviewed_sha = con.execute(
                """
                SELECT head_sha
                FROM review_runs
                WHERE repo = ? AND pr_number = ?
                ORDER BY reviewed_at DESC, id DESC
                LIMIT 1
                """,
                (repo, pr_number),
            ).fetchone()
            rows = con.execute(
                """
                SELECT *
                FROM findings
                WHERE repo = ? AND pr_number = ?
                ORDER BY last_seen_at DESC, id DESC
                """,
                (repo, pr_number),
            ).fetchall()

        return PullRequestMemoryHistory(
            repo=repo,
            pr_number=pr_number,
            last_reviewed_sha=str(last_reviewed_sha["head_sha"]) if last_reviewed_sha else None,
            previous_findings=[_record_from_row(row) for row in rows],
        )

    def export_repo(self, repo: str) -> dict[str, Any]:
        """Return deterministic, secret-free memory data for one repository."""
        with self._connect() as con:
            run_rows = con.execute(
                """
                SELECT id, repo, pr_number, head_sha, reviewed_at,
                       context_loaded, comments_posted
                FROM review_runs
                WHERE repo = ?
                ORDER BY pr_number ASC, reviewed_at ASC, id ASC
                """,
                (repo,),
            ).fetchall()
            finding_rows = con.execute(
                """
                SELECT *
                FROM findings
                WHERE repo = ?
                ORDER BY pr_number ASC, last_seen_at DESC, id DESC
                """,
                (repo,),
            ).fetchall()
            learning_rows = con.execute(
                """
                SELECT *
                FROM learnings
                WHERE repo = ?
                ORDER BY active DESC, created_at ASC, id ASC
                """,
                (repo,),
            ).fetchall()

        return {
            "schema_version": 1,
            "repo": repo,
            "review_runs": [
                {
                    "id": int(row["id"]),
                    "pr_number": int(row["pr_number"]),
                    "head_sha": str(row["head_sha"]),
                    "reviewed_at": str(row["reviewed_at"]),
                    "context_loaded": bool(row["context_loaded"]),
                    "comments_posted": bool(row["comments_posted"]),
                }
                for row in run_rows
            ],
            "findings": [_export_finding_row(row) for row in finding_rows],
            "learnings": [_export_learning_row(row) for row in learning_rows],
        }

    def add_learning(
        self,
        *,
        repo: str,
        instruction: str,
        scope: str = "repository",
        source_pr_number: int | None = None,
        source_comment_id: int | None = None,
        source_url: str = "",
        author: str = "",
        created_at: datetime | None = None,
        active: bool = True,
    ) -> LearningMemoryRecord:
        """Store one explicit repository learning."""
        clean_instruction = _clean_instruction(instruction)
        clean_scope = _clean_scope(scope)
        now = created_at or _now()
        with self._connect() as con:
            cursor = con.execute(
                """
                INSERT INTO learnings (
                    repo, scope, instruction, source_pr_number, source_comment_id,
                    source_url, author, created_at, active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo,
                    clean_scope,
                    clean_instruction,
                    source_pr_number,
                    source_comment_id,
                    source_url,
                    author,
                    _dump_dt(now),
                    int(active),
                ),
            )
            learning_id = int(cursor.lastrowid or 0)
            con.commit()
        return LearningMemoryRecord(
            id=learning_id,
            repo=repo,
            scope=clean_scope,
            instruction=clean_instruction,
            source_pr_number=source_pr_number,
            source_comment_id=source_comment_id,
            source_url=source_url,
            author=author,
            created_at=now,
            active=active,
        )

    def list_learnings(
        self,
        repo: str,
        *,
        active_only: bool = True,
        limit: int = 20,
    ) -> list[LearningMemoryRecord]:
        """Return stored explicit learnings for one repository."""
        where = "WHERE repo = ? AND active = 1" if active_only else "WHERE repo = ?"
        bounded_limit = max(1, min(limit, 100))
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT *
                FROM learnings
                {where}
                ORDER BY active DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                (repo, bounded_limit),
            ).fetchall()
        return [_learning_from_row(row) for row in rows]

    def prune_before(self, repo: str, cutoff: datetime) -> dict[str, int]:
        """Delete repository memory rows older than ``cutoff``."""
        cutoff_text = _dump_dt(cutoff)
        with self._connect() as con:
            run_count = int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM review_runs
                    WHERE repo = ? AND reviewed_at < ?
                    """,
                    (repo, cutoff_text),
                ).fetchone()[0]
            )
            finding_count = int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM findings
                    WHERE repo = ? AND last_seen_at < ?
                    """,
                    (repo, cutoff_text),
                ).fetchone()[0]
            )
            con.execute(
                """
                DELETE FROM review_runs
                WHERE repo = ? AND reviewed_at < ?
                """,
                (repo, cutoff_text),
            )
            con.execute(
                """
                DELETE FROM findings
                WHERE repo = ? AND last_seen_at < ?
                """,
                (repo, cutoff_text),
            )
            con.commit()
        return {"review_runs": run_count, "findings": finding_count}

    def compare_with_history(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        current_findings: Iterable[Finding],
    ) -> FindingComparison:
        """Compare current findings to stored memory without writing a run."""
        history = self.load_history(repo, pr_number)
        previous = {record.fingerprint: record for record in history.previous_findings}
        current_records: list[FindingMemoryRecord] = []
        seen: set[str] = set()
        now = _now()

        for finding in current_findings:
            fingerprint = fingerprint_finding(finding)
            seen.add(fingerprint)
            old = previous.get(fingerprint)
            status = FindingStatus.STILL_PRESENT if old else FindingStatus.NEW
            current_records.append(
                _record_from_finding(
                    finding,
                    fingerprint=fingerprint,
                    status=status,
                    first_seen_sha=old.first_seen_sha if old else head_sha,
                    last_seen_sha=head_sha,
                    first_seen_at=old.first_seen_at if old else now,
                    last_seen_at=now,
                )
            )

        resolved = [
            _replace_status(record, FindingStatus.POSSIBLY_FIXED, head_sha=head_sha, now=now)
            for fingerprint, record in previous.items()
            if fingerprint not in seen
        ]
        return FindingComparison(current=current_records, resolved=resolved)

    def record_review(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        findings: Iterable[Finding],
        context_loaded: bool,
        comments_posted: bool,
    ) -> ReviewMemoryWrite:
        """Persist one review run and its finding comparison."""
        findings_list = list(findings)
        comparison = self.compare_with_history(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            current_findings=findings_list,
        )
        now = _now()
        with self._connect() as con:
            cursor = con.execute(
                """
                INSERT INTO review_runs
                    (repo, pr_number, head_sha, reviewed_at, context_loaded, comments_posted)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    repo,
                    pr_number,
                    head_sha,
                    _dump_dt(now),
                    int(context_loaded),
                    int(comments_posted),
                ),
            )
            review_id = int(cursor.lastrowid or 0)
            for record in comparison.current:
                _upsert_finding(con, repo, pr_number, record)
            for record in comparison.resolved:
                _upsert_finding(con, repo, pr_number, record)
            con.commit()
        return ReviewMemoryWrite(review_id=review_id, comparison=comparison)

    def _initialise(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS review_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    context_loaded INTEGER NOT NULL,
                    comments_posted INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_review_runs_pr
                    ON review_runs(repo, pr_number, reviewed_at);

                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    file TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    suggestion TEXT NOT NULL,
                    first_seen_sha TEXT NOT NULL,
                    last_seen_sha TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(repo, pr_number, fingerprint)
                );

                CREATE TABLE IF NOT EXISTS learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    source_pr_number INTEGER,
                    source_comment_id INTEGER,
                    source_url TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_learnings_repo_active
                    ON learnings(repo, active, created_at);
                """)
            con.commit()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con


def _upsert_finding(
    con: sqlite3.Connection,
    repo: str,
    pr_number: int,
    record: FindingMemoryRecord,
) -> None:
    con.execute(
        """
        INSERT INTO findings (
            repo, pr_number, fingerprint, status, title, category, severity,
            file, line, reason, suggestion, first_seen_sha, last_seen_sha,
            first_seen_at, last_seen_at, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, pr_number, fingerprint) DO UPDATE SET
            status = excluded.status,
            title = excluded.title,
            category = excluded.category,
            severity = excluded.severity,
            file = excluded.file,
            line = excluded.line,
            reason = excluded.reason,
            suggestion = excluded.suggestion,
            last_seen_sha = excluded.last_seen_sha,
            last_seen_at = excluded.last_seen_at,
            payload_json = excluded.payload_json
        """,
        (
            repo,
            pr_number,
            record.fingerprint,
            record.status.value,
            record.title,
            record.category,
            record.severity,
            record.file,
            record.line,
            record.reason,
            record.suggestion,
            record.first_seen_sha,
            record.last_seen_sha,
            _dump_dt(record.first_seen_at),
            _dump_dt(record.last_seen_at),
            json.dumps(record.payload, sort_keys=True),
        ),
    )


def _record_from_finding(
    finding: Finding,
    *,
    fingerprint: str,
    status: FindingStatus,
    first_seen_sha: str,
    last_seen_sha: str,
    first_seen_at: datetime,
    last_seen_at: datetime,
) -> FindingMemoryRecord:
    return FindingMemoryRecord(
        fingerprint=fingerprint,
        status=status,
        title=finding.title,
        category=finding.category,
        severity=finding.severity.name,
        file=finding.file,
        line=finding.line,
        reason=finding.reason,
        suggestion=finding.suggestion,
        first_seen_sha=first_seen_sha,
        last_seen_sha=last_seen_sha,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
        payload=finding_payload(finding),
    )


def _record_from_row(row: sqlite3.Row) -> FindingMemoryRecord:
    return FindingMemoryRecord(
        fingerprint=str(row["fingerprint"]),
        status=FindingStatus(str(row["status"])),
        title=str(row["title"]),
        category=str(row["category"]),
        severity=str(row["severity"]),
        file=str(row["file"]),
        line=int(row["line"]),
        reason=str(row["reason"]),
        suggestion=str(row["suggestion"]),
        first_seen_sha=str(row["first_seen_sha"]),
        last_seen_sha=str(row["last_seen_sha"]),
        first_seen_at=_load_dt(str(row["first_seen_at"])),
        last_seen_at=_load_dt(str(row["last_seen_at"])),
        payload=_load_json(str(row["payload_json"])),
    )


def _export_finding_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "pr_number": int(row["pr_number"]),
        "fingerprint": str(row["fingerprint"]),
        "status": str(row["status"]),
        "title": str(row["title"]),
        "category": str(row["category"]),
        "severity": str(row["severity"]),
        "file": str(row["file"]),
        "line": int(row["line"]),
        "reason": str(row["reason"]),
        "suggestion": str(row["suggestion"]),
        "first_seen_sha": str(row["first_seen_sha"]),
        "last_seen_sha": str(row["last_seen_sha"]),
        "first_seen_at": str(row["first_seen_at"]),
        "last_seen_at": str(row["last_seen_at"]),
    }


def _export_learning_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "scope": str(row["scope"]),
        "instruction": str(row["instruction"]),
        "source_pr_number": _optional_int(row["source_pr_number"]),
        "source_comment_id": _optional_int(row["source_comment_id"]),
        "source_url": str(row["source_url"]),
        "author": str(row["author"]),
        "created_at": str(row["created_at"]),
        "active": bool(row["active"]),
    }


def _learning_from_row(row: sqlite3.Row) -> LearningMemoryRecord:
    return LearningMemoryRecord(
        id=int(row["id"]),
        repo=str(row["repo"]),
        scope=str(row["scope"]),
        instruction=str(row["instruction"]),
        source_pr_number=_optional_int(row["source_pr_number"]),
        source_comment_id=_optional_int(row["source_comment_id"]),
        source_url=str(row["source_url"]),
        author=str(row["author"]),
        created_at=_load_dt(str(row["created_at"])),
        active=bool(row["active"]),
    )


def _replace_status(
    record: FindingMemoryRecord,
    status: FindingStatus,
    *,
    head_sha: str,
    now: datetime,
) -> FindingMemoryRecord:
    return FindingMemoryRecord(
        fingerprint=record.fingerprint,
        status=status,
        title=record.title,
        category=record.category,
        severity=record.severity,
        file=record.file,
        line=record.line,
        reason=record.reason,
        suggestion=record.suggestion,
        first_seen_sha=record.first_seen_sha,
        last_seen_sha=head_sha,
        first_seen_at=record.first_seen_at,
        last_seen_at=now,
        payload=record.payload,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _dump_dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _load_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _load_json(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


def _clean_instruction(value: str) -> str:
    instruction = " ".join(value.split())
    if not instruction:
        raise ValueError("learning instruction must not be empty")
    if len(instruction) > 1000:
        raise ValueError("learning instruction must be 1000 characters or fewer")
    return instruction


def _clean_scope(value: str) -> str:
    scope = value.strip().lower() or "repository"
    if scope != "repository":
        raise ValueError("only repository-scoped learnings are supported")
    return scope


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
