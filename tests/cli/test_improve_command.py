"""Tests for ``cli.commands.improve``."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from agents.prompting import format_prompt_diff
from cli.commands.improve import ImprovementSuggestion, render_improvements, run_improve
from cli.main import app
from configs import load_settings
from configs.schema import AstInstruction
from configs.settings import Settings
from memory.store import SQLitePullRequestMemory
from rag.retriever import RetrievalResult

_BASE = "https://api.github.com"
_RUNNER = CliRunner()
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
async def test_run_improve_returns_grounded_suggestions(scaffold_repo: Path) -> None:
    _mock_pr()
    captured: list[object] = []

    async def fake_generator(*args: object, **_kwargs: object) -> list[ImprovementSuggestion]:
        captured.append(args[0])
        return [
            ImprovementSuggestion(
                file="src/search.py",
                line=1,
                title="Validate query",
                reason="The changed search path accepts a new query value.",
                suggestion="Guard against an empty query before returning.",
                fix="if not query:\n    return []",
            )
        ]

    settings = load_settings(scaffold_repo, env={})

    summary = await run_improve(
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
    assert summary["suggestions_count"] == 1
    assert summary["dropped_suggestions_count"] == 0
    assert summary["suggestions"][0]["title"] == "Validate query"
    assert summary["suggestions"][0]["line"] == 1


@respx.mock
async def test_run_improve_uses_prepared_controls_for_grounding_and_publish(
    scaffold_repo: Path,
) -> None:
    _mock_controlled_pr()
    context_payloads: list[object] = []
    generator_payloads: list[object] = []
    published: list[dict[str, object]] = []

    async def capture_context(payload: object) -> None:
        context_payloads.append(payload)
        return None

    async def fake_generator(
        *args: object,
        **_kwargs: object,
    ) -> list[ImprovementSuggestion]:
        generator_payloads.append(args[0])
        return [
            ImprovementSuggestion(
                file="src/search.py",
                line=2,
                title="Validate query",
                reason="The changed query is returned without validation.",
                suggestion="Validate the query before returning it.",
                fix="return validate(query)",
            ),
            ImprovementSuggestion(
                file="docs/hidden.py",
                line=1,
                title="Excluded suggestion",
                reason="This path is excluded from review.",
                suggestion="Do not publish this suggestion.",
                fix="hidden = 3",
            ),
        ]

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})
    _enable_ast_controls(settings)

    summary = await run_improve(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=capture_context,
        publish=True,
        publisher=fake_publisher,
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
    assert summary["dropped_suggestions_count"] == 1
    assert len(published) == 1
    inline = published[0]["inline_suggestions"]
    assert [item.path for item in inline] == ["src/search.py"]
    assert published[0]["summary_suggestions"] == []


@respx.mock
async def test_run_improve_drops_ungrounded_suggestions(scaffold_repo: Path) -> None:
    _mock_pr()

    async def fake_generator(*_args: object, **_kwargs: object) -> list[ImprovementSuggestion]:
        return [
            ImprovementSuggestion(
                file="src/search.py",
                line=1,
                title="Grounded",
                reason="Changed line.",
                suggestion="Keep this.",
            ),
            ImprovementSuggestion(
                file="src/search.py",
                line=99,
                title="Wrong line",
                reason="Not a changed line.",
                suggestion="Drop this.",
            ),
            ImprovementSuggestion(
                file="src/other.py",
                line=1,
                title="Wrong file",
                reason="Not a changed file.",
                suggestion="Drop this too.",
            ),
        ]

    settings = load_settings(scaffold_repo, env={})

    summary = await run_improve(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
    )

    assert summary["suggestions_count"] == 1
    assert summary["dropped_suggestions_count"] == 2
    assert summary["suggestions"][0]["title"] == "Grounded"


@respx.mock
async def test_run_improve_passes_loaded_context(scaffold_repo: Path) -> None:
    _mock_pr()
    retrieval = RetrievalResult(tests=[{"score": 0.9, "payload": {"name": "tests"}}])
    captured: list[object] = []

    async def fake_context_loader(_payload: object) -> RetrievalResult:
        return retrieval

    async def fake_generator(*_args: object, **kwargs: object) -> list[ImprovementSuggestion]:
        captured.append(kwargs.get("retrieval_result"))
        return []

    settings = load_settings(scaffold_repo, env={})

    summary = await run_improve(
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
async def test_run_improve_passes_active_learnings(scaffold_repo: Path) -> None:
    _mock_pr()
    captured: list[object] = []
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.add_learning(repo="o/r", instruction="Prefer concrete replacements over TODOs.")

    async def fake_generator(*_args: object, **kwargs: object) -> list[ImprovementSuggestion]:
        captured.append(kwargs.get("pr_history"))
        return []

    await run_improve(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
    )

    assert captured
    history = captured[0]
    assert history.learnings[0].instruction == "Prefer concrete replacements over TODOs."


@respx.mock
async def test_run_improve_dry_run_does_not_publish(scaffold_repo: Path) -> None:
    _mock_pr()

    async def fake_generator(*_args: object, **_kwargs: object) -> list[ImprovementSuggestion]:
        return [
            ImprovementSuggestion(
                file="src/search.py",
                line=1,
                title="Validate query",
                reason="The changed search path accepts a new query value.",
                suggestion="Guard against an empty query before returning.",
                fix="if not query:\n    return []",
            )
        ]

    published: list[dict[str, object]] = []

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})

    summary = await run_improve(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
        publisher=fake_publisher,
    )

    assert summary["publish_status"] == "dry_run"
    assert summary["published_inline_count"] == 0
    assert summary["published_summary_count"] == 0
    assert published == []


@respx.mock
async def test_run_improve_publish_posts_inline_and_summary_suggestions(
    scaffold_repo: Path,
) -> None:
    _mock_pr()

    async def fake_generator(*_args: object, **_kwargs: object) -> list[ImprovementSuggestion]:
        return [
            ImprovementSuggestion(
                file="src/search.py",
                line=1,
                title="Validate query",
                reason="The changed search path accepts a new query value.",
                suggestion="Guard against an empty query before returning.",
                fix="if not query:\n    return []",
            ),
            ImprovementSuggestion(
                file="src/search.py",
                line=2,
                title="Normalize query",
                reason="The returned value should be normalized consistently.",
                suggestion="Strip surrounding whitespace before using the query.",
            ),
            ImprovementSuggestion(
                file="src/search.py",
                line=2,
                title="Add TODO",
                reason="A TODO comment would remind maintainers to revisit this later.",
                suggestion="Add a TODO comment above this return.",
                fix="# TODO: revisit search behavior",
            ),
        ]

    published: list[dict[str, object]] = []

    async def fake_publisher(**kwargs: object) -> None:
        published.append(kwargs)

    settings = load_settings(scaffold_repo, env={})

    summary = await run_improve(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
        publish=True,
        publisher=fake_publisher,
    )

    assert summary["publish_status"] == "posted"
    assert summary["published_inline_count"] == 1
    assert summary["published_summary_count"] == 1
    assert summary["dropped_actionability_count"] == 1
    assert len(published) == 1
    assert published[0]["head_sha"] == "abcdef0123456789" + "0" * 24

    inline = published[0]["inline_suggestions"]
    summary_suggestions = published[0]["summary_suggestions"]
    assert len(inline) == 1
    assert "```suggestion" in inline[0].body
    assert inline[0].path == "src/search.py"
    assert inline[0].line == 1
    assert len(summary_suggestions) == 1
    assert summary_suggestions[0].title == "Normalize query"


def test_cli_improve_accepts_publish_flags(scaffold_repo: Path) -> None:
    dry = _RUNNER.invoke(
        app,
        [
            "improve",
            "--pr",
            "42",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "o/r",
            "--dry-run",
        ],
    )
    publish = _RUNNER.invoke(
        app,
        [
            "improve",
            "--pr",
            "42",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "o/r",
            "--publish",
        ],
    )

    assert dry.exit_code != 2
    assert publish.exit_code != 2


def test_render_improvements_prints_sections() -> None:
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
        "suggestions_count": 1,
        "dropped_suggestions_count": 2,
        "dropped_actionability_count": 0,
        "publish_status": "dry_run",
        "published_inline_count": 0,
        "published_summary_count": 0,
        "suggestions": [
            {
                "file": "src/search.py",
                "line": 1,
                "title": "Validate query",
                "reason": "The changed search path accepts a new query value.",
                "suggestion": "Guard against an empty query before returning.",
                "fix": "if not query:\n    return []",
            }
        ],
    }
    out = io.StringIO()

    render_improvements(summary, out)

    text = out.getvalue()
    assert "PR #42 on o/r" in text
    assert "Suggestions:  1" in text
    assert "Dropped:      2 ungrounded" in text
    assert "Context:      loaded" in text
    assert "Published:    no (dry run)" in text
    assert "Improvement suggestions:" in text
    assert "Validate query (src/search.py:1)" in text
    assert "Fix:" in text
