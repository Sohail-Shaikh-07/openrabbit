"""Tests for ``cli.commands.review``."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import respx
from typer.testing import CliRunner

from agents.models import Finding, ReviewState, Severity
from agents.prompting import collect_context, collect_history_context, format_prompt_diff
from cli.commands.review import render_summary, run_review
from cli.commands.review_pipeline import ReviewPipelineResult
from cli.main import app
from configs import load_settings
from configs.schema import AstInstruction
from configs.settings import Settings
from github_ import GitHubAPIError
from memory.models import FindingComparison, PullRequestMemoryHistory, ReviewMemoryWrite
from memory.store import SQLitePullRequestMemory
from quality.models import ToolDiagnostic, ToolRunResult, ToolStatus
from rag.retriever import RetrievalResult
from ranking.ranker import RankedFinding

_BASE = "https://api.github.com"
_RUNNER = CliRunner()
_AST_INSTRUCTION = "Validate query input before use."
_EXPECTED_AST_PROMPT = (
    "- AST instructions:\n"
    "  - src/search.py:1-2 [python function search]\n"
    f"    {_AST_INSTRUCTION}"
)


class _LegacyMemoryBackend:
    def __init__(self) -> None:
        self.compare_calls: list[list[Finding]] = []
        self.record_calls: list[list[Finding]] = []

    def load_history(self, repo: str, pr_number: int) -> PullRequestMemoryHistory:
        return PullRequestMemoryHistory(repo=repo, pr_number=pr_number)

    def compare_with_history(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        current_findings: Iterable[Finding],
    ) -> FindingComparison:
        del repo, pr_number, head_sha
        findings = list(current_findings)
        self.compare_calls.append(findings)
        return FindingComparison(current=[], resolved=[])

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
        del repo, pr_number, head_sha, context_loaded, comments_posted
        findings_list = list(findings)
        self.record_calls.append(findings_list)
        return ReviewMemoryWrite(review_id=1, comparison=FindingComparison(current=[], resolved=[]))


def _pr_json() -> dict[str, object]:
    return {
        "number": 42,
        "title": "Big PR",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "feat", "sha": "abcdef0123456789" + "0" * 24, "label": "alice:feat"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "labels": [],
        "body": "Body",
        "merged": False,
    }


def _mock_controlled_pr() -> None:
    source = b"def search(query):\n    return query\n"
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/search.py",
                    "status": "modified",
                    "additions": 2,
                    "deletions": 1,
                    "changes": 3,
                    "patch": (
                        "@@ -1,1 +1,2 @@\n-def search():\n+def search(query):\n"
                        "+    return query\n"
                    ),
                },
                {
                    "filename": "docs/hidden.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                    "changes": 2,
                    "patch": "@@ -1,1 +1,1 @@\n-old = 1\n+hidden = 2\n",
                },
                {
                    "filename": "assets/logo.png",
                    "status": "added",
                    "additions": 0,
                    "deletions": 0,
                    "changes": 0,
                },
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    respx.get(f"{_BASE}/repos/o/r/contents/src/search.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(source).decode(),
                "size": len(source),
            },
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/reviews").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 10,
                    "user": {"login": "reviewer", "id": 2},
                    "body": "GENERAL_CONVERSATION_CONTEXT",
                    "state": "COMMENTED",
                    "commit_id": "c" * 40,
                    "submitted_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://example/review/10",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 20,
                    "user": {"login": "reviewer", "id": 2},
                    "body": "SKIPPED_CONVERSATION_CONTEXT",
                    "path": "docs/hidden.py",
                    "line": 1,
                    "commit_id": "c" * 40,
                    "created_at": "2026-01-01T00:01:00Z",
                    "updated_at": "2026-01-01T00:01:00Z",
                    "html_url": "https://example/comment/20",
                },
                {
                    "id": 21,
                    "user": {"login": "reviewer", "id": 2},
                    "body": "UNCHANGED_CONVERSATION_CONTEXT",
                    "path": "docs/architecture.md",
                    "line": 8,
                    "commit_id": "c" * 40,
                    "created_at": "2026-01-01T00:02:00Z",
                    "updated_at": "2026-01-01T00:02:00Z",
                    "html_url": "https://example/comment/21",
                },
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/issues/42/comments").mock(
        return_value=httpx.Response(200, json=[])
    )


def _context_retrieval() -> RetrievalResult:
    return RetrievalResult(
        security=[
            {
                "score": 0.9,
                "payload": {
                    "source_path": "docs/hidden.py",
                    "text": "SKIPPED_RETRIEVAL_CONTEXT",
                },
            },
            {
                "score": 0.8,
                "payload": {
                    "source_path": "src/search.py",
                    "text": "ALLOWED_RETRIEVAL_CONTEXT",
                },
            },
            {
                "score": 0.7,
                "payload": {
                    "source_path": "docs/architecture.md",
                    "text": "UNCHANGED_RETRIEVAL_CONTEXT",
                },
            },
            {"score": 0.6, "payload": {"text": "GENERAL_RETRIEVAL_CONTEXT"}},
        ]
    )


def _context_finding(path: str, title: str) -> Finding:
    return Finding(
        severity=Severity.high,
        category="bug",
        file=path,
        line=1,
        confidence=0.9,
        title=title,
        reason=f"{title} reason",
        suggestion=f"{title} suggestion",
    )


def _enable_ast_controls(settings: Settings) -> None:
    settings.review.path_include = ["src/**"]
    settings.review.ast_instructions = [
        AstInstruction(
            path="src/**",
            languages=["python"],
            symbols=["function"],
            name_pattern="search",
            instructions=_AST_INSTRUCTION,
        )
    ]


async def _empty_context_loader(_payload: object) -> None:
    return None


@respx.mock
async def test_run_review_returns_summary(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                },
                {
                    "filename": "logo.png",
                    "status": "added",
                    "additions": 0,
                    "deletions": 0,
                    "changes": 0,
                },
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        run_agents=False,
    )

    assert summary["repo"] == "o/r"
    assert summary["number"] == 42
    assert summary["title"] == "Big PR"
    assert summary["state"] == "open"
    assert summary["files_changed"] == 2
    assert summary["binary_files"] == 1
    assert summary["hunks"] == 1
    assert summary["commits"] == 1
    assert summary["head_sha"] == "abcdef012345"


@respx.mock
async def test_run_review_adapts_legacy_memory_backend_signatures(scaffold_repo: Path) -> None:
    _mock_controlled_pr()
    backend = _LegacyMemoryBackend()
    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        run_agents=False,
        dry_run=True,
        memory_store=backend,
    )

    assert len(backend.compare_calls) == 1
    assert len(backend.record_calls) == 1
    assert summary["memory_error"] is None


@respx.mock
async def test_run_review_returns_ranked_findings_from_agent_runner(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    finding = Finding(
        severity=Severity.high,
        category="bug",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="Missing guard",
        reason="value can be None",
        suggestion="Add a guard",
        fix="",
    )

    captured_quality: list[object] = []

    async def fake_runner(*_args: object, **kwargs: object) -> ReviewPipelineResult:
        captured_quality.extend(kwargs.get("quality_results") or [])
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
            dropped_findings_count=2,
        )

    async def fake_quality_runner(*_args: object) -> list[ToolRunResult]:
        return [
            ToolRunResult(
                tool="ruff",
                status=ToolStatus.failed,
                command=("python", "-m", "ruff"),
                exit_code=1,
                duration_ms=4.0,
                summary="1 diagnostic",
                diagnostics=(
                    ToolDiagnostic(
                        severity="error",
                        message="Undefined name",
                        file="src/a.py",
                        line=2,
                        code="F821",
                    ),
                ),
            )
        ]

    settings = load_settings(scaffold_repo, env={})
    settings.quality.enabled = True

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=_empty_context_loader,
        quality_gate_runner=fake_quality_runner,
        dry_run=True,
    )

    assert summary["findings_count"] == 1
    assert summary["dropped_findings_count"] == 2
    assert summary["findings"][0]["title"] == "Missing guard"
    assert summary["findings"][0]["score"] == 2.7
    assert summary["comments_posted"] is False
    assert summary["publish_status"] == "dry_run"
    assert [result.tool for result in captured_quality if isinstance(result, ToolRunResult)] == [
        "ruff"
    ]
    assert summary["quality_status_counts"] == {"failed": 1}
    assert summary["quality_diagnostics_count"] == 1
    assert summary["quality_gates"][0]["diagnostics"][0]["code"] == "F821"


@respx.mock
async def test_run_review_returns_skipped_path_summary(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                },
                {
                    "filename": "docs/usage.md",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                },
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[],
            skipped_paths=[{"path": "docs/usage.md", "reason": "path_not_included"}],
        )

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        dry_run=True,
        agent_runner=fake_runner,
        context_loader=_empty_context_loader,
    )

    assert summary["skipped_paths_count"] == 1
    assert summary["skipped_paths"] == [{"path": "docs/usage.md", "reason": "path_not_included"}]


@respx.mock
async def test_run_review_reuses_prepared_controls_across_downstream_steps(
    scaffold_repo: Path,
) -> None:
    _mock_controlled_pr()
    context_paths: list[list[str]] = []
    runner_payloads: list[object] = []
    model_contexts: list[str] = []
    published: list[dict[str, object]] = []
    allowed_finding = _context_finding("src/search.py", "Validate query")
    skipped_finding = _context_finding("docs/hidden.py", "Excluded finding")

    async def capture_context(payload: Any) -> RetrievalResult:
        context_paths.append([file_.path for file_ in payload.files])
        return _context_retrieval()

    async def strict_runner(
        pr_payload: Any,
        *,
        settings: Settings,
        retrieval_result: Any | None,
        pr_history: Any | None,
        quality_results: list[ToolRunResult],
        env: dict[str, str] | None,
    ) -> ReviewPipelineResult:
        del settings, env
        runner_payloads.append(pr_payload)
        state: ReviewState = {
            "pr_payload": pr_payload,
            "retrieval_result": retrieval_result,
            "pr_history": pr_history,
            "quality_results": quality_results,
        }
        model_contexts.append(
            "\n".join(
                [
                    collect_context(state, "security"),
                    collect_history_context(state),
                ]
            )
        )
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[
                RankedFinding(finding=allowed_finding, score=2.7),
                RankedFinding(finding=skipped_finding, score=2.7),
            ],
        )

    async def fake_quality_runner(*_args: object) -> list[ToolRunResult]:
        return [
            ToolRunResult(
                tool="ruff",
                status=ToolStatus.failed,
                command=("ruff", "check"),
                exit_code=1,
                duration_ms=1.0,
                summary="four diagnostics",
                diagnostics=(
                    ToolDiagnostic(
                        message="SKIPPED_QUALITY_CONTEXT", file="docs/hidden.py", severity="error"
                    ),
                    ToolDiagnostic(
                        message="ALLOWED_QUALITY_CONTEXT", file="src/search.py", severity="error"
                    ),
                    ToolDiagnostic(
                        message="UNCHANGED_QUALITY_CONTEXT",
                        file="docs/architecture.md",
                        severity="warning",
                    ),
                    ToolDiagnostic(message="GENERAL_QUALITY_CONTEXT", severity="warning"),
                ),
            )
        ]

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})
    _enable_ast_controls(settings)
    settings.quality.enabled = True
    store = SQLitePullRequestMemory(scaffold_repo / ".openrabbit" / "state" / "controls.db")
    store.record_review(
        repo="o/r",
        pr_number=42,
        head_sha="previous-sha",
        findings=[
            _context_finding("docs/hidden.py", "SKIPPED_PREVIOUS_FINDING"),
            _context_finding("src/search.py", "ALLOWED_PREVIOUS_FINDING"),
            _context_finding("docs/architecture.md", "UNCHANGED_PREVIOUS_FINDING"),
        ],
        context_loaded=True,
        comments_posted=False,
    )

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=strict_runner,
        context_loader=capture_context,
        publisher=fake_publisher,
        memory_store=store,
        quality_gate_runner=fake_quality_runner,
    )

    assert context_paths == [["src/search.py"]]
    assert [file_.path for file_ in runner_payloads[0].files] == ["src/search.py"]
    assert _EXPECTED_AST_PROMPT in format_prompt_diff(runner_payloads[0])
    model_context = model_contexts[0]
    assert "SKIPPED_RETRIEVAL_CONTEXT" not in model_context
    assert "SKIPPED_PREVIOUS_FINDING" not in model_context
    assert "SKIPPED_CONVERSATION_CONTEXT" not in model_context
    assert "SKIPPED_QUALITY_CONTEXT" not in model_context
    for expected in (
        "ALLOWED_RETRIEVAL_CONTEXT",
        "UNCHANGED_RETRIEVAL_CONTEXT",
        "GENERAL_RETRIEVAL_CONTEXT",
        "ALLOWED_PREVIOUS_FINDING",
        "UNCHANGED_PREVIOUS_FINDING",
        "UNCHANGED_CONVERSATION_CONTEXT",
        "GENERAL_CONVERSATION_CONTEXT",
        "ALLOWED_QUALITY_CONTEXT",
        "UNCHANGED_QUALITY_CONTEXT",
        "GENERAL_QUALITY_CONTEXT",
    ):
        assert expected in model_context
    assert summary["files_changed"] == 3
    assert summary["binary_files"] == 1
    assert summary["hunks"] == 2
    assert summary["skipped_paths_count"] == 2
    assert summary["ast_instruction_count"] == 1
    assert summary["review_control_warning_count"] == 0
    assert summary["review_control_warnings"] == []
    assert summary["ast_unsupported_path_count"] == 0
    assert summary["dropped_findings_count"] == 1
    assert summary["quality_diagnostics_count"] == 4
    assert len(published) == 1
    ranked = published[0]["ranked"]
    assert [item.finding.file for item in ranked] == ["src/search.py"]


@respx.mock
async def test_run_review_preserves_skipped_memory_without_suppressing_later_incremental(
    scaffold_repo: Path,
) -> None:
    _mock_controlled_pr()
    prior = _context_finding("docs/hidden.py", "Prior hidden finding")
    store = SQLitePullRequestMemory(scaffold_repo / ".openrabbit" / "state" / "scoped.db")
    store.record_review(
        repo="o/r",
        pr_number=42,
        head_sha="previous-sha",
        findings=[prior],
        context_loaded=False,
        comments_posted=False,
    )
    published: list[dict[str, object]] = []

    async def empty_runner(
        pr_payload: Any,
        *,
        settings: Settings,
        retrieval_result: Any | None,
        pr_history: Any | None,
        quality_results: list[ToolRunResult],
        env: dict[str, str] | None,
    ) -> ReviewPipelineResult:
        del pr_payload, settings, retrieval_result, pr_history, quality_results, env
        return ReviewPipelineResult(agent_results=[], ranked_findings=[])

    async def finding_runner(
        pr_payload: Any,
        *,
        settings: Settings,
        retrieval_result: Any | None,
        pr_history: Any | None,
        quality_results: list[ToolRunResult],
        env: dict[str, str] | None,
    ) -> ReviewPipelineResult:
        del pr_payload, settings, retrieval_result, pr_history, quality_results, env
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=prior, score=2.7)],
        )

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})
    settings.review.path_include = ["src/**"]
    scoped = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=empty_runner,
        context_loader=_empty_context_loader,
        memory_store=store,
        mode="incremental",
    )

    retained = store.load_history("o/r", 42).previous_findings[0]
    assert retained.status.value == "new"
    assert retained.last_seen_sha == "previous-sha"
    assert scoped["findings"] == []
    assert scoped["memory_status_counts"] == {}

    settings.review.path_include = []
    later = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=finding_runner,
        context_loader=_empty_context_loader,
        memory_store=store,
        publisher=fake_publisher,
        mode="incremental",
    )

    assert later["findings"][0]["memory_status"] == "new"
    assert later["publish_status"] == "posted"
    assert len(published) == 1


@respx.mock
async def test_run_review_records_local_memory_status(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    finding = Finding(
        severity=Severity.high,
        category="security",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="SQL injection in query builder",
        reason="Raw SQL is built from input.",
        suggestion="Use bind parameters.",
    )
    captured_history: list[object] = []

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        captured_history.append(_kwargs.get("pr_history"))
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
        )

    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(scaffold_repo / ".openrabbit" / "state" / "test.db")
    store.add_learning(repo="o/r", instruction="Prefer bind parameters for raw SQL.")

    first = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=_empty_context_loader,
        dry_run=True,
        memory_store=store,
    )
    second = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=_empty_context_loader,
        dry_run=True,
        memory_store=store,
    )

    first_history = captured_history[0]
    second_history = captured_history[1]
    assert first["memory_enabled"] is True
    assert first["memory_context"] == "loaded"
    assert first["learning_count"] == 1
    assert first_history is not None
    assert second_history is not None
    assert first_history.learnings[0].instruction == "Prefer bind parameters for raw SQL."
    assert first_history.local.last_reviewed_sha is None
    assert second_history.local.last_reviewed_sha == "abcdef0123456789" + "0" * 24
    assert first["memory_status_counts"] == {"new": 1}
    assert first["findings"][0]["memory_status"] == "new"
    assert second["memory_status_counts"] == {"still_present": 1}
    assert second["findings"][0]["memory_status"] == "still_present"
    assert second["last_reviewed_sha"] == "abcdef0123456789" + "0" * 24


@respx.mock
async def test_run_review_passes_github_conversation_history(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/reviews").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 10,
                    "user": {"login": "reviewer", "id": 2},
                    "body": "Please fix the query.",
                    "state": "COMMENTED",
                    "commit_id": "c" * 40,
                    "submitted_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://github.com/o/r/pull/42#pullrequestreview-10",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 20,
                    "user": {"login": "reviewer", "id": 2},
                    "body": "This line still uses token=secret-token-value.",
                    "path": "src/a.py",
                    "line": 2,
                    "commit_id": "c" * 40,
                    "created_at": "2026-01-01T00:01:00Z",
                    "updated_at": "2026-01-01T00:02:00Z",
                    "html_url": "https://github.com/o/r/pull/42#discussion_r20",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/issues/42/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 30,
                    "user": {"login": "author", "id": 3},
                    "body": "Fixed in the latest commit.",
                    "created_at": "2026-01-01T00:03:00Z",
                    "updated_at": "2026-01-01T00:04:00Z",
                    "html_url": "https://github.com/o/r/pull/42#issuecomment-30",
                }
            ],
        )
    )
    captured_history: list[object] = []

    async def fake_runner(*_args: object, **kwargs: object) -> ReviewPipelineResult:
        captured_history.append(kwargs.get("pr_history"))
        return ReviewPipelineResult(agent_results=[], ranked_findings=[])

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=_empty_context_loader,
        dry_run=True,
    )

    history = captured_history[0]
    assert history is not None
    assert summary["conversation_count"] == 3
    assert [event.source for event in history.conversation] == [
        "review",
        "review_comment",
        "issue_comment",
    ]
    assert "token=[REDACTED]" in history.conversation[1].body
    assert "secret-token-value" not in history.conversation[1].body


@respx.mock
async def test_run_review_incremental_mode_suppresses_repeated_publish(
    scaffold_repo: Path,
) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    finding = Finding(
        severity=Severity.high,
        category="security",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="SQL injection in query builder",
        reason="Raw SQL is built from input.",
        suggestion="Use bind parameters.",
    )

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
        )

    published: list[dict[str, object]] = []

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(scaffold_repo / ".openrabbit" / "state" / "test.db")

    first = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        publisher=fake_publisher,
        context_loader=_empty_context_loader,
        memory_store=store,
        mode="incremental",
    )
    second = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        publisher=fake_publisher,
        context_loader=_empty_context_loader,
        memory_store=store,
        mode="incremental",
    )

    assert first["publish_status"] == "posted"
    assert second["publish_status"] == "no_new_findings"
    assert second["comments_posted"] is False
    assert second["published_findings_count"] == 0
    assert len(published) == 1


@respx.mock
async def test_run_review_full_mode_reposts_repeated_findings(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    finding = Finding(
        severity=Severity.high,
        category="security",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="SQL injection in query builder",
        reason="Raw SQL is built from input.",
        suggestion="Use bind parameters.",
    )

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
        )

    published: list[dict[str, object]] = []

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(scaffold_repo / ".openrabbit" / "state" / "test.db")

    await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        publisher=fake_publisher,
        context_loader=_empty_context_loader,
        memory_store=store,
        mode="full",
    )
    second = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        publisher=fake_publisher,
        context_loader=_empty_context_loader,
        memory_store=store,
        mode="full",
    )

    assert second["publish_status"] == "posted"
    assert second["published_findings_count"] == 1
    assert len(published) == 2


@respx.mock
async def test_run_review_publishes_ranked_findings_when_not_dry_run(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    finding = Finding(
        severity=Severity.high,
        category="bug",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="Missing guard",
        reason="value can be None",
        suggestion="Add a guard",
        fix="",
    )

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
        )

    published: list[dict[str, object]] = []

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        publisher=fake_publisher,
        context_loader=_empty_context_loader,
    )

    assert summary["comments_posted"] is True
    assert summary["publish_status"] == "posted"
    assert len(published) == 1
    assert published[0]["pr_number"] == 42
    assert published[0]["head_sha"] == "abcdef0123456789" + "0" * 24
    assert len(published[0]["ranked"]) == 1


@respx.mock
async def test_run_review_uses_github_publisher_by_default(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    review_route = respx.post(f"{_BASE}/repos/o/r/pulls/42/reviews").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 123,
                "state": "COMMENTED",
                "html_url": "https://github.com/o/r/pull/42#pullrequestreview-123",
            },
        )
    )
    finding = Finding(
        severity=Severity.high,
        category="bug",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="Missing guard",
        reason="value can be None",
        suggestion="Add a guard",
        fix="",
    )

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
        )

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=_empty_context_loader,
    )

    assert summary["comments_posted"] is True
    assert review_route.called
    payload = json.loads(review_route.calls[0].request.content)
    assert payload["commit_id"] == "abcdef0123456789" + "0" * 24
    assert payload["event"] == "COMMENT"
    assert len(payload["comments"]) == 1


@respx.mock
async def test_run_review_dry_run_never_publishes(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    finding = Finding(
        severity=Severity.high,
        category="bug",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="Missing guard",
        reason="value can be None",
        suggestion="Add a guard",
        fix="",
    )

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
        )

    published: list[dict[str, object]] = []

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        publisher=fake_publisher,
        context_loader=_empty_context_loader,
        dry_run=True,
    )

    assert summary["comments_posted"] is False
    assert summary["publish_status"] == "dry_run"
    assert published == []


@respx.mock
async def test_run_review_does_not_publish_empty_findings(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(return_value=httpx.Response(200, json=[]))

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(agent_results=[], ranked_findings=[])

    published: list[dict[str, object]] = []

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        publisher=fake_publisher,
        context_loader=_empty_context_loader,
    )

    assert summary["comments_posted"] is False
    assert summary["publish_status"] == "no_findings"
    assert published == []


@respx.mock
async def test_run_review_surfaces_publisher_errors(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "changes": 1,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )
    finding = Finding(
        severity=Severity.high,
        category="bug",
        file="src/a.py",
        line=2,
        confidence=0.9,
        title="Missing guard",
        reason="value can be None",
        suggestion="Add a guard",
        fix="",
    )

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
        )

    async def fake_publisher(**_kwargs: object) -> None:
        raise GitHubAPIError(422, "line is invalid")

    settings = load_settings(scaffold_repo, env={})

    try:
        await run_review(
            settings,
            number=42,
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
            agent_runner=fake_runner,
            publisher=fake_publisher,
            context_loader=_empty_context_loader,
        )
    except GitHubAPIError as exc:
        assert exc.status_code == 422
        assert "line is invalid" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected GitHubAPIError")


@respx.mock
async def test_run_review_passes_loaded_context_to_agent_runner(scaffold_repo: Path) -> None:
    pr_json = _pr_json()
    pr_json["body"] = "Adds linked issue context. Fixes #12."
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=pr_json))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/issues/12").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 12,
                "title": "Need linked issue context",
                "state": "open",
                "body": "Review should see linked issue intent.",
                "labels": [{"name": "context"}],
                "html_url": "https://github.com/o/r/issues/12",
            },
        )
    )
    retrieval = RetrievalResult(
        security=[
            {
                "score": 0.9,
                "payload": {
                    "name": "rule",
                    "source_path": "services/api/AGENTS.md",
                    "rule_source": "repository_guideline",
                    "guideline_path": "services/api/AGENTS.md",
                    "scope_path": "services/api",
                },
            }
        ]
    )
    captured: list[object] = []

    async def fake_context_loader(_payload: object) -> RetrievalResult:
        return retrieval

    async def fake_runner(*_args: object, **kwargs: object) -> ReviewPipelineResult:
        captured.append(kwargs.get("retrieval_result"))
        return ReviewPipelineResult(agent_results=[], ranked_findings=[])

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=fake_context_loader,
        dry_run=True,
    )

    assert captured == [retrieval]
    assert summary["context_loaded"] is True
    assert summary["context_provenance"] == [
        {
            "dimension": "security",
            "source_path": "services/api/AGENTS.md",
            "name": "rule",
            "kind": "",
            "score": 0.9,
            "rule_source": "repository_guideline",
            "scope_path": "services/api",
            "guideline_path": "services/api/AGENTS.md",
        }
    ]
    assert summary["guideline_sources"] == ["services/api/AGENTS.md"]
    assert summary["linked_issue_count"] == 1


@respx.mock
async def test_run_review_marks_empty_context_as_diff_only(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(return_value=httpx.Response(200, json=[]))

    async def fake_context_loader(_payload: object) -> RetrievalResult:
        return RetrievalResult()

    async def fake_runner(*_args: object, **kwargs: object) -> ReviewPipelineResult:
        assert isinstance(kwargs.get("retrieval_result"), RetrievalResult)
        return ReviewPipelineResult(agent_results=[], ranked_findings=[])

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=fake_context_loader,
        dry_run=True,
    )

    assert summary["context_loaded"] is False


@respx.mock
async def test_run_review_continues_when_context_loader_fails(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(return_value=httpx.Response(200, json=[]))
    captured: list[object] = []

    async def failing_context_loader(_payload: object) -> RetrievalResult:
        raise RuntimeError("qdrant down")

    async def fake_runner(*_args: object, **kwargs: object) -> ReviewPipelineResult:
        captured.append(kwargs.get("retrieval_result"))
        return ReviewPipelineResult(agent_results=[], ranked_findings=[])

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=failing_context_loader,
        dry_run=True,
    )

    assert captured == [None]
    assert summary["context_loaded"] is False


@respx.mock
async def test_run_review_loads_repository_guidelines_when_rag_is_unavailable(
    scaffold_repo: Path,
) -> None:
    _mock_controlled_pr()
    (scaffold_repo / "AGENTS.md").write_text(
        "# Repository rules\n\nRequire explicit validation for search inputs.\n",
        encoding="utf-8",
    )
    captured: list[object] = []

    async def fake_runner(*_args: object, **kwargs: object) -> ReviewPipelineResult:
        captured.append(kwargs.get("retrieval_result"))
        return ReviewPipelineResult(agent_results=[], ranked_findings=[])

    settings = load_settings(scaffold_repo, env={})

    summary = await run_review(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        agent_runner=fake_runner,
        context_loader=_empty_context_loader,
        dry_run=True,
    )

    retrieval = captured[0]
    assert isinstance(retrieval, RetrievalResult)
    assert retrieval.security
    assert "Require explicit validation" in retrieval.security[0]["payload"]["text"]
    assert summary["context_loaded"] is True
    assert summary["guideline_sources"] == ["AGENTS.md"]


def test_render_summary_prints_every_field() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 3,
        "binary_files": 1,
        "hunks": 5,
        "commits": 2,
        "findings_count": 0,
        "dropped_findings_count": 0,
        "context_loaded": False,
        "findings": [],
        "mode": "incremental",
        "published_findings_count": 0,
        "publish_status": "no_findings",
        "ast_instruction_count": 0,
        "review_control_warning_count": 0,
        "ast_unsupported_path_count": 0,
    }
    out = io.StringIO()
    render_summary(summary, out)
    text = out.getvalue()
    assert "PR #7 on o/r" in text
    assert "Hello" in text
    assert "abcdef012345" in text
    assert "3 (1 binary)" in text
    assert "Hunks:" in text
    assert "Commits:" in text
    assert "Context:      diff only" in text
    assert "no findings to post" in text
    assert "AST rules:" not in text
    assert "Control warnings:" not in text
    assert "Unsupported AST files:" not in text


def test_cli_review_accepts_mode_flag(scaffold_repo: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "review",
            "--pr",
            "42",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "o/r",
            "--mode",
            "full",
            "--dry-run",
        ],
    )

    assert result.exit_code != 2


def test_render_summary_prints_findings() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 1,
        "binary_files": 0,
        "hunks": 1,
        "commits": 1,
        "findings_count": 1,
        "dropped_findings_count": 3,
        "context_loaded": True,
        "publish_status": "posted",
        "findings": [
            {
                "severity": "high",
                "category": "bug",
                "file": "src/a.py",
                "line": 2,
                "confidence": 0.9,
                "title": "Missing guard",
                "reason": "value can be None",
                "suggestion": "Add a guard",
                "fix": "",
                "score": 2.7,
            }
        ],
    }
    out = io.StringIO()
    render_summary(summary, out)

    text = out.getvalue()
    assert "Findings:     1" in text
    assert "Dropped:      3 ungrounded" in text
    assert "Context:      loaded" in text
    assert "Published:    yes" in text
    assert "[HIGH] Missing guard" in text
    assert "src/a.py:2" in text


def test_render_summary_prints_context_provenance() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 1,
        "binary_files": 0,
        "hunks": 1,
        "commits": 1,
        "findings_count": 0,
        "dropped_findings_count": 0,
        "context_loaded": True,
        "context_provenance": [
            {
                "dimension": "security",
                "source_path": ".openrabbit/security.md",
                "name": "security rules",
                "kind": "section",
                "score": 0.91,
                "retrieval_reason": "scoped_guideline",
            },
            {
                "dimension": "architecture",
                "source_path": "docs/architecture.md",
                "name": "request flow",
                "kind": "section",
                "score": 0.82,
            },
        ],
        "publish_status": "no_findings",
        "findings": [],
    }
    out = io.StringIO()
    render_summary(summary, out)

    text = out.getvalue()
    assert "Context:      loaded" in text
    assert "Context sources:" in text
    assert "security .openrabbit/security.md" in text
    assert "reason=scoped_guideline" in text
    assert "architecture docs/architecture.md" in text


def test_render_summary_prints_memory_status_counts() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 1,
        "binary_files": 0,
        "hunks": 1,
        "commits": 1,
        "findings_count": 0,
        "dropped_findings_count": 0,
        "context_loaded": False,
        "mode": "incremental",
        "memory_enabled": True,
        "last_reviewed_sha": "oldabcdef012345",
        "memory_status_counts": {"possibly_fixed": 1, "stale": 2},
        "conversation_count": 1,
        "publish_status": "no_new_findings",
        "findings": [],
    }
    out = io.StringIO()
    render_summary(summary, out)

    text = out.getvalue()
    assert "Memory:       enabled" in text
    assert "Last review:  oldabcdef012" in text
    assert "Statuses:     possibly_fixed=1, stale=2" in text
    assert "Conversation: 1 event" in text


def test_render_summary_prints_skipped_paths() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 3,
        "binary_files": 0,
        "hunks": 1,
        "commits": 1,
        "findings_count": 0,
        "dropped_findings_count": 0,
        "context_loaded": False,
        "skipped_paths_count": 2,
        "skipped_paths": [
            {"path": "docs/usage.md", "reason": "path_not_included"},
            {"path": "dist/app.js", "reason": "generated"},
        ],
        "publish_status": "no_findings",
        "ast_instruction_count": 2,
        "review_control_warning_count": 1,
        "ast_unsupported_path_count": 3,
        "findings": [],
    }
    out = io.StringIO()
    render_summary(summary, out)

    text = out.getvalue()
    assert "Skipped:     2 paths" in text
    assert "docs/usage.md (path_not_included)" in text
    assert "dist/app.js (generated)" in text
    assert "AST rules: 2 matched" in text
    assert "Control warnings: 1" in text
    assert "Unsupported AST files: 3" in text
