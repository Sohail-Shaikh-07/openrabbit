"""Tests for ``cli.commands.describe``."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import respx

from agents.prompting import format_prompt_diff
from cli.commands.describe import (
    PullRequestDescription,
    render_description,
    render_description_json,
    render_description_markdown,
    run_describe,
)
from cli.commands.pr_summary import SUMMARY_MARKER, PRSummaryPublishResult
from configs import load_settings
from configs.schema import AstInstruction
from configs.settings import Settings
from memory.store import SQLitePullRequestMemory
from rag.retriever import RetrievalResult

_BASE = "https://api.github.com"
_AST_INSTRUCTION = "Validate query input before use."
_EXPECTED_AST_PROMPT = (
    "- AST instructions:\n"
    "  - src/search.py:1-2 [python function search]\n"
    f"    {_AST_INSTRUCTION}"
)


def _pr_json() -> dict[str, object]:
    return {
        "number": 42,
        "title": "Improve search",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "feat/search", "sha": "abcdef0123456789" + "0" * 24, "label": "a:feat"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "labels": [],
        "body": "Body",
        "merged": False,
    }


def _mock_pr() -> None:
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
                    "patch": "@@ -1,2 +1,3 @@\n-def search():\n+def search(query):\n+    return query\n",
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
async def test_run_describe_returns_read_only_summary(scaffold_repo: Path) -> None:
    _mock_pr()
    captured: list[object] = []

    async def fake_generator(*args: object, **_kwargs: object) -> PullRequestDescription:
        captured.append(args[0])
        return PullRequestDescription(
            summary="Search now accepts a query.",
            changed_files=["src/search.py updates the search signature."],
            risk_areas=["Search behavior changed for callers."],
            testing_focus=["Verify empty and populated query paths."],
            walkthrough=[{"file": "src/search.py", "notes": "Adds query handling."}],
        )

    settings = load_settings(scaffold_repo, env={})

    summary = await run_describe(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
    )

    assert captured
    assert summary["repo"] == "o/r"
    assert summary["number"] == 42
    assert summary["title"] == "Improve search"
    assert summary["files_changed"] == 2
    assert summary["binary_files"] == 1
    assert summary["hunks"] == 1
    assert summary["context_loaded"] is False
    assert summary["description"]["summary"] == "Search now accepts a query."


@respx.mock
async def test_run_describe_uses_prepared_controls_for_context_model_and_publish(
    scaffold_repo: Path,
) -> None:
    _mock_controlled_pr()
    context_payloads: list[object] = []
    generator_payloads: list[object] = []
    published_summaries: list[dict[str, object]] = []

    async def capture_context(payload: object) -> None:
        context_payloads.append(payload)
        return None

    async def fake_generator(*args: object, **_kwargs: object) -> PullRequestDescription:
        generator_payloads.append(args[0])
        paths = [file_.path for file_ in args[0].files]
        return PullRequestDescription(summary=", ".join(paths), changed_files=paths)

    async def fake_publisher(
        _handle: object,
        *,
        pr_number: int,
        summary: dict[str, object],
    ) -> PRSummaryPublishResult:
        assert pr_number == 42
        published_summaries.append(summary)
        return PRSummaryPublishResult(
            action="created", comment_id=90, html_url="https://example/90"
        )

    settings = load_settings(scaffold_repo, env={})
    _enable_ast_controls(settings)

    summary = await run_describe(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=capture_context,
        publish=True,
        summary_publisher=fake_publisher,
    )

    assert generator_payloads == context_payloads
    assert [file_.path for file_ in generator_payloads[0].files] == ["src/search.py"]
    assert _EXPECTED_AST_PROMPT in format_prompt_diff(generator_payloads[0])
    assert summary["files_changed"] == 3
    assert summary["binary_files"] == 1
    assert summary["hunks"] == 2
    assert summary["ast_instruction_count"] == 1
    assert summary["review_control_warning_count"] == 0
    assert summary["review_control_warnings"] == []
    assert summary["ast_unsupported_path_count"] == 0
    assert published_summaries == [summary]
    assert summary["description"]["changed_files"] == ["src/search.py"]


@respx.mock
async def test_run_describe_passes_loaded_context(scaffold_repo: Path) -> None:
    _mock_pr()
    retrieval = RetrievalResult(architecture=[{"score": 0.9, "payload": {"name": "arch"}}])
    captured: list[object] = []

    async def fake_context_loader(_payload: object) -> RetrievalResult:
        return retrieval

    async def fake_generator(*_args: object, **kwargs: object) -> PullRequestDescription:
        captured.append(kwargs.get("retrieval_result"))
        return PullRequestDescription(summary="Summary")

    settings = load_settings(scaffold_repo, env={})

    summary = await run_describe(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=fake_context_loader,
    )

    assert captured == [retrieval]
    assert summary["context_loaded"] is True


@respx.mock
async def test_run_describe_passes_active_learnings(scaffold_repo: Path) -> None:
    _mock_pr()
    captured: list[object] = []
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.add_learning(repo="o/r", instruction="Prefer repository-layer SQL access.")

    async def fake_generator(*_args: object, **kwargs: object) -> PullRequestDescription:
        captured.append(kwargs.get("pr_history"))
        return PullRequestDescription(summary="Summary")

    await run_describe(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
    )

    assert captured
    history = captured[0]
    assert history.learnings[0].instruction == "Prefer repository-layer SQL access."


@respx.mock
async def test_run_describe_publish_creates_managed_summary(scaffold_repo: Path) -> None:
    _mock_pr()
    captured: dict[str, object] = {}

    async def fake_generator(*_args: object, **_kwargs: object) -> PullRequestDescription:
        return PullRequestDescription(
            summary="Search now accepts a query.",
            risk_areas=["src/search.py changes caller behavior."],
            testing_focus=["Exercise empty query input."],
            walkthrough=[{"file": "src/search.py", "notes": "Adds query handling."}],
        )

    respx.get(f"{_BASE}/repos/o/r/issues/42/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": 90,
                "user": {"login": "openrabbit", "id": 42},
                "body": "created",
                "html_url": "https://github.com/o/r/pull/42#issuecomment-90",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        )

    respx.post(f"{_BASE}/repos/o/r/issues/42/comments").mock(side_effect=handler)
    settings = load_settings(scaffold_repo, env={})

    summary = await run_describe(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
        publish=True,
    )

    assert summary["publish_status"] == "created"
    assert summary["summary_comment_id"] == 90
    assert SUMMARY_MARKER in str(captured["body"])
    assert "### Walkthrough" in str(captured["body"])
    assert "@openrabbit review" in str(captured["body"])


@respx.mock
async def test_run_describe_publish_updates_existing_managed_summary(
    scaffold_repo: Path,
) -> None:
    _mock_pr()
    captured: dict[str, object] = {}

    async def fake_generator(*_args: object, **_kwargs: object) -> PullRequestDescription:
        return PullRequestDescription(summary="Updated summary.")

    respx.get(f"{_BASE}/repos/o/r/issues/42/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 91,
                    "user": {"login": "openrabbit", "id": 42},
                    "body": f"{SUMMARY_MARKER}\nold body",
                    "html_url": "https://github.com/o/r/pull/42#issuecomment-91",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": 91,
                "user": {"login": "openrabbit", "id": 42},
                "body": "updated",
                "html_url": "https://github.com/o/r/pull/42#issuecomment-91",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:05:00Z",
            },
        )

    respx.patch(f"{_BASE}/repos/o/r/issues/comments/91").mock(side_effect=handler)
    settings = load_settings(scaffold_repo, env={})

    summary = await run_describe(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
        publish=True,
    )

    assert summary["publish_status"] == "updated"
    assert "Updated summary." in str(captured["body"])


def test_render_description_prints_sections() -> None:
    summary = {
        "repo": "o/r",
        "number": 42,
        "title": "Improve search",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 1,
        "binary_files": 0,
        "hunks": 1,
        "commits": 1,
        "context_loaded": True,
        "description": {
            "summary": "Search now accepts a query.",
            "changed_files": ["src/search.py updates query handling."],
            "risk_areas": ["Caller compatibility."],
            "testing_focus": ["Empty query."],
            "walkthrough": [{"file": "src/search.py", "notes": "Adds query handling."}],
        },
    }
    out = io.StringIO()

    render_description(summary, out)

    text = out.getvalue()
    assert "PR #42 on o/r" in text
    assert "Context:      loaded" in text
    assert "Summary:" in text
    assert "Changed files:" in text
    assert "Risk areas:" in text
    assert "Testing focus:" in text
    assert "Walkthrough:" in text
    assert "src/search.py: Adds query handling." in text


def test_render_description_markdown_prints_report_sections() -> None:
    summary = {
        "repo": "o/r",
        "number": 42,
        "title": "Improve search",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 1,
        "binary_files": 0,
        "hunks": 1,
        "commits": 1,
        "context_loaded": False,
        "description": {
            "summary": "Search now accepts a query.",
            "changed_files": ["src/search.py updates query handling."],
            "risk_areas": ["Caller compatibility."],
            "testing_focus": ["Empty query."],
            "walkthrough": [{"file": "src/search.py", "notes": "Adds query handling."}],
        },
    }
    out = io.StringIO()

    render_description_markdown(summary, out)

    text = out.getvalue()
    assert "# PR #42: Improve search" in text
    assert "- Context: diff only" in text
    assert "## Changed Files" in text
    assert "- `src/search.py`: Adds query handling." in text


def test_render_description_json_prints_deterministic_summary() -> None:
    summary = {
        "repo": "o/r",
        "number": 42,
        "title": "Improve search",
        "description": {"summary": "Search now accepts a query."},
    }
    out = io.StringIO()

    render_description_json(summary, out)

    text = out.getvalue()
    assert text.endswith("\n")
    assert '"description": {' in text
    assert '"number": 42' in text
