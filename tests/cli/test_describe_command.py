"""Tests for ``cli.commands.describe``."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import respx

from cli.commands.describe import PullRequestDescription, render_description, run_describe
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
