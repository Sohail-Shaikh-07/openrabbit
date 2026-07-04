"""Tests for ``cli.commands.improve``."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import respx

from cli.commands.improve import ImprovementSuggestion, render_improvements, run_improve
from configs import load_settings
from rag.retriever import RetrievalResult

_BASE = "https://api.github.com"


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
    assert "Improvement suggestions:" in text
    assert "Validate query (src/search.py:1)" in text
    assert "Fix:" in text
