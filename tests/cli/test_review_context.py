from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from agents.models import Finding, Severity
from cli.commands.review import _exclude_skipped_ranked
from cli.commands.review_context import filter_model_review_context
from memory.history import ConversationEvent, PullRequestHistory
from memory.models import (
    FindingMemoryRecord,
    FindingStatus,
    PullRequestMemoryHistory,
)
from quality.models import ToolDiagnostic, ToolRunResult, ToolStatus
from rag.retriever import RetrievalResult
from ranking.ranker import RankedFinding
from review_controls import ReviewControlResult, SkippedPath


def _controls(*skipped: str) -> ReviewControlResult:
    return ReviewControlResult(
        filtered_payload=SimpleNamespace(files=[SimpleNamespace(path="src/allowed.py")]),
        skipped_paths=[SkippedPath(path=path, reason="path_excluded") for path in skipped],
    )


def _finding(path: str) -> Finding:
    return Finding(
        severity=Severity.high,
        category="bug",
        file=path,
        line=1,
        confidence=0.9,
        title=path,
        reason="reason",
        suggestion="suggestion",
    )


def _record(path: str) -> FindingMemoryRecord:
    now = datetime.now(UTC)
    return FindingMemoryRecord(
        fingerprint=path,
        status=FindingStatus.NEW,
        title=path,
        category="bug",
        severity="high",
        file=path,
        line=1,
        reason="reason",
        suggestion="suggestion",
        first_seen_sha="old",
        last_seen_sha="old",
        first_seen_at=now,
        last_seen_at=now,
    )


def test_model_context_and_injected_findings_match_diff_prefixes_and_backslashes() -> None:
    controls = _controls("docs/hidden.py")
    retrieval = RetrievalResult(
        security=[
            {"payload": {"source_path": "a/docs/hidden.py"}},
            {"payload": {"source_path": "b/docs/hidden.py"}},
            {"payload": {"source_path": r"docs\hidden.py"}},
            {"payload": {"source_path": "docs/architecture.md"}},
            {"payload": {"text": "general context"}},
        ]
    )
    history = PullRequestHistory(
        repo="owner/repo",
        pr_number=1,
        head_sha="head",
        local=PullRequestMemoryHistory(
            repo="owner/repo",
            pr_number=1,
            previous_findings=[
                _record("a/docs/hidden.py"),
                _record("b/docs/hidden.py"),
                _record(r"docs\hidden.py"),
                _record("docs/architecture.md"),
            ],
        ),
        conversation=[
            ConversationEvent(
                source="review_comment",
                author="alice",
                body="hidden",
                url="",
                file="b/docs/hidden.py",
                line=1,
            ),
            ConversationEvent(
                source="review_comment",
                author="alice",
                body="unchanged",
                url="",
                file="docs/architecture.md",
                line=1,
            ),
            ConversationEvent(source="issue_comment", author="alice", body="general", url=""),
        ],
    )
    quality = [
        ToolRunResult(
            tool="ruff",
            status=ToolStatus.failed,
            command=("ruff",),
            exit_code=1,
            duration_ms=1,
            summary="diagnostics",
            diagnostics=(
                ToolDiagnostic(severity="error", message="hidden", file="a/docs/hidden.py"),
                ToolDiagnostic(
                    severity="warning", message="unchanged", file="docs/architecture.md"
                ),
                ToolDiagnostic(severity="warning", message="general"),
            ),
        )
    ]

    result = filter_model_review_context(
        controls,
        retrieval_result=retrieval,
        pr_history=history,
        quality_results=quality,
    )

    assert [hit["payload"].get("source_path") for hit in result.retrieval_result.security] == [
        "docs/architecture.md",
        None,
    ]
    assert [record.file for record in result.pr_history.local.previous_findings] == [
        "docs/architecture.md"
    ]
    assert [event.file for event in result.pr_history.conversation] == [
        "docs/architecture.md",
        "",
    ]
    assert [diagnostic.file for diagnostic in result.quality_results[0].diagnostics] == [
        "docs/architecture.md",
        "",
    ]

    ranked = [
        RankedFinding(finding=_finding("a/docs/hidden.py"), score=1),
        RankedFinding(finding=_finding("b/docs/hidden.py"), score=1),
        RankedFinding(finding=_finding(r"docs\hidden.py"), score=1),
        RankedFinding(finding=_finding("docs/architecture.md"), score=1),
    ]
    kept, dropped = _exclude_skipped_ranked(
        ranked,
        {"docs/hidden.py"},
        repository_paths={"docs/hidden.py", "src/allowed.py"},
    )
    assert [item.finding.file for item in kept] == ["docs/architecture.md"]
    assert dropped == 3


def test_top_level_a_and_b_paths_are_not_rewritten_without_diff_alias_match() -> None:
    controls = _controls("a/docs/hidden.py", "b/docs/hidden.py")
    retrieval = RetrievalResult(
        security=[
            {"payload": {"source_path": "a/docs/hidden.py"}},
            {"payload": {"source_path": "docs/hidden.py"}},
        ]
    )

    result = filter_model_review_context(
        controls,
        retrieval_result=retrieval,
        pr_history=None,
        quality_results=[],
    )

    assert [hit["payload"]["source_path"] for hit in result.retrieval_result.security] == [
        "docs/hidden.py"
    ]
