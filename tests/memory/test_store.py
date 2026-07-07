"""Tests for the SQLite-backed PR memory store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agents.models import Finding, Severity
from memory.models import FindingStatus
from memory.store import SQLitePullRequestMemory


def _finding(title: str = "SQL Injection vulnerability") -> Finding:
    return Finding(
        severity=Severity.high,
        category="security",
        file="app/repositories/task_repository.py",
        line=74,
        confidence=0.9,
        title=title,
        reason="Raw SQL is built from user input.",
        suggestion="Use bind parameters.",
    )


def test_record_review_persists_structured_history(tmp_path: Path) -> None:
    db_path = tmp_path / "openrabbit.db"
    store = SQLitePullRequestMemory(db_path)
    finding = _finding()

    result = store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[finding],
        context_loaded=True,
        comments_posted=False,
    )
    history = store.load_history("owner/repo", 7)

    assert db_path.exists()
    assert result.review_id > 0
    assert history.repo == "owner/repo"
    assert history.pr_number == 7
    assert history.last_reviewed_sha == "abc123"
    assert len(history.previous_findings) == 1
    assert history.previous_findings[0].title == "SQL Injection vulnerability"
    assert history.previous_findings[0].status == FindingStatus.NEW


def test_classify_current_findings_marks_repeated_and_missing_items(tmp_path: Path) -> None:
    store = SQLitePullRequestMemory(tmp_path / "openrabbit.db")
    old_finding = _finding()
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="oldsha",
        findings=[old_finding],
        context_loaded=False,
        comments_posted=True,
    )

    repeated = _finding(title="Potential SQL injection via raw SQL")
    comparison = store.compare_with_history(
        repo="owner/repo",
        pr_number=7,
        head_sha="newsha",
        current_findings=[repeated],
    )

    assert comparison.current[0].status == FindingStatus.STILL_PRESENT
    assert comparison.resolved == []

    fixed = store.compare_with_history(
        repo="owner/repo",
        pr_number=7,
        head_sha="fixedsha",
        current_findings=[],
    )

    assert fixed.current == []
    assert len(fixed.resolved) == 1
    assert fixed.resolved[0].status == FindingStatus.POSSIBLY_FIXED


def test_record_review_persists_resolved_finding_status(tmp_path: Path) -> None:
    store = SQLitePullRequestMemory(tmp_path / "openrabbit.db")
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="oldsha",
        findings=[_finding()],
        context_loaded=False,
        comments_posted=True,
    )

    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="fixedsha",
        findings=[],
        context_loaded=False,
        comments_posted=False,
    )

    history = store.load_history("owner/repo", 7)

    assert len(history.previous_findings) == 1
    assert history.previous_findings[0].status == FindingStatus.POSSIBLY_FIXED
    assert history.previous_findings[0].last_seen_sha == "fixedsha"


def test_export_repo_returns_deterministic_secret_free_payload(tmp_path: Path) -> None:
    store = SQLitePullRequestMemory(tmp_path / "openrabbit.db")
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )

    payload = store.export_repo("owner/repo")

    assert payload["schema_version"] == 1
    assert payload["repo"] == "owner/repo"
    assert payload["review_runs"][0]["pr_number"] == 7  # type: ignore[index]
    assert payload["review_runs"][0]["context_loaded"] is True  # type: ignore[index]
    assert payload["findings"][0]["title"] == "SQL Injection vulnerability"  # type: ignore[index]
    assert "payload_json" not in payload["findings"][0]  # type: ignore[index]


def test_prune_before_deletes_old_runs_and_findings(tmp_path: Path) -> None:
    store = SQLitePullRequestMemory(tmp_path / "openrabbit.db")
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )

    deleted = store.prune_before("owner/repo", cutoff=_far_future())
    payload = store.export_repo("owner/repo")

    assert deleted == {"review_runs": 1, "findings": 1}
    assert payload["review_runs"] == []
    assert payload["findings"] == []


def _far_future() -> datetime:
    return datetime(2999, 1, 1, tzinfo=UTC)
