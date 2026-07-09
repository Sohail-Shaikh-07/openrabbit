"""Tests for PR history assembly and formatting."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from memory.history import (
    ConversationEvent,
    PullRequestHistory,
    conversation_events_from_github,
    format_history_context,
    sanitize_conversation_body,
)
from memory.models import (
    FindingMemoryRecord,
    FindingStatus,
    LearningMemoryRecord,
    PullRequestMemoryHistory,
)


def _record(status: FindingStatus) -> FindingMemoryRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return FindingMemoryRecord(
        fingerprint="abc",
        status=status,
        title="Raw SQL construction from changed input",
        category="security",
        severity="high",
        file="app/repositories/task_repository.py",
        line=74,
        reason="Raw SQL is built from user input.",
        suggestion="Use bind parameters.",
        first_seen_sha="oldsha",
        last_seen_sha="newsha",
        first_seen_at=now,
        last_seen_at=now,
    )


def test_format_history_context_summarizes_local_memory_and_conversation() -> None:
    history = PullRequestHistory(
        repo="owner/repo",
        pr_number=7,
        head_sha="newsha",
        commit_shas=["oldsha", "newsha"],
        local=PullRequestMemoryHistory(
            repo="owner/repo",
            pr_number=7,
            last_reviewed_sha="oldsha",
            previous_findings=[_record(FindingStatus.STILL_PRESENT)],
        ),
        conversation=[
            ConversationEvent(
                source="issue_comment",
                author="alice",
                body="Fixed the query in the latest commit.",
                url="https://github.com/owner/repo/pull/7#issuecomment-1",
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        ],
        learnings=[
            LearningMemoryRecord(
                id=1,
                repo="owner/repo",
                scope="repository",
                instruction="Prefer SQLAlchemy bind parameters for raw SQL.",
                source_pr_number=7,
                source_comment_id=123,
                source_url="https://github.com/owner/repo/pull/7#issuecomment-123",
                author="alice",
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        ],
    )

    text = format_history_context(history)

    assert "Last reviewed SHA: oldsha" in text
    assert "Raw SQL construction from changed input" in text
    assert "still_present" in text
    assert "alice" in text
    assert "Fixed the query" in text
    assert "Active repository learnings" in text
    assert "Prefer SQLAlchemy bind parameters" in text


def test_pull_request_history_from_payload_collects_commit_shas() -> None:
    payload = SimpleNamespace(
        number=3,
        head_sha="headsha",
        commits=[SimpleNamespace(sha="one"), SimpleNamespace(sha="two")],
    )

    history = PullRequestHistory.from_payload(
        repo="owner/repo",
        payload=payload,
        local=PullRequestMemoryHistory(repo="owner/repo", pr_number=3),
    )

    assert history.repo == "owner/repo"
    assert history.pr_number == 3
    assert history.head_sha == "headsha"
    assert history.commit_shas == ["one", "two"]


def test_conversation_events_from_github_sorts_and_sanitizes() -> None:
    review = SimpleNamespace(
        body="Please do not paste token=super-secret-value-here in logs.",
        user=SimpleNamespace(login="reviewer"),
        html_url="https://github.com/owner/repo/pull/3#pullrequestreview-1",
        submitted_at=datetime(2026, 1, 2, tzinfo=UTC),
        state="COMMENTED",
        commit_id="abc",
    )
    issue_comment = SimpleNamespace(
        body="Fixed it. github_pat_abcdefghijklmnopqrstuvwxyz0123456789",
        user=SimpleNamespace(login="author"),
        html_url="https://github.com/owner/repo/pull/3#issuecomment-2",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    events = conversation_events_from_github(
        reviews=[review],
        review_comments=[],
        issue_comments=[issue_comment],
    )

    assert [event.source for event in events] == ["issue_comment", "review"]
    assert events[0].author == "author"
    assert "[REDACTED]" in events[0].body
    assert "github_pat_" not in events[0].body
    assert "token=[REDACTED]" in events[1].body


def test_sanitize_conversation_body_bounds_large_comments() -> None:
    body = sanitize_conversation_body("x" * 2000)

    assert len(body) == 1200
    assert body.endswith("...")
