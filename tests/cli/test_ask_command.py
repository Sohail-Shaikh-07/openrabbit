"""Tests for ``cli.commands.ask``."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
import respx

from cli.commands.ask import AnswerEvidence, PullRequestAnswer, render_answer, run_ask
from configs import load_settings
from memory.store import SQLitePullRequestMemory
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
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "c" * 40, "commit": {"message": "msg"}}])
    )


async def _empty_context_loader(_payload: object) -> None:
    return None


@respx.mock
async def test_run_ask_returns_evidence_based_answer(scaffold_repo: Path) -> None:
    _mock_pr()
    captured_questions: list[str] = []

    async def fake_generator(*_args: object, **kwargs: object) -> PullRequestAnswer:
        captured_questions.append(str(kwargs["question"]))
        return PullRequestAnswer(
            answer="The PR changes search to accept a query and return it directly.",
            evidence=[
                AnswerEvidence(
                    source="changed_lines",
                    file="src/search.py",
                    line=1,
                    detail="The search function signature now accepts query.",
                )
            ],
            uncertainty=["No caller changes are visible in this PR."],
            follow_up_checks=["Run search tests for empty and populated queries."],
        )

    settings = load_settings(scaffold_repo, env={})

    summary = await run_ask(
        settings,
        number=42,
        question=" What changed in search? ",
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
    )

    assert captured_questions == ["What changed in search?"]
    assert summary["repo"] == "o/r"
    assert summary["number"] == 42
    assert summary["question"] == "What changed in search?"
    assert summary["context_loaded"] is False
    assert summary["answer"]["answer"].startswith("The PR changes search")
    assert summary["answer"]["evidence"][0]["file"] == "src/search.py"


@respx.mock
async def test_run_ask_passes_loaded_context(scaffold_repo: Path) -> None:
    _mock_pr()
    retrieval = RetrievalResult(architecture=[{"score": 0.9, "payload": {"name": "arch"}}])
    captured: list[object] = []

    async def fake_context_loader(_payload: object) -> RetrievalResult:
        return retrieval

    async def fake_generator(*_args: object, **kwargs: object) -> PullRequestAnswer:
        captured.append(kwargs.get("retrieval_result"))
        return PullRequestAnswer(answer="Context-aware answer.")

    settings = load_settings(scaffold_repo, env={})

    summary = await run_ask(
        settings,
        number=42,
        question="Does this match the architecture?",
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=fake_context_loader,
    )

    assert captured == [retrieval]
    assert summary["context_loaded"] is True


@respx.mock
async def test_run_ask_passes_active_learnings(scaffold_repo: Path) -> None:
    _mock_pr()
    captured: list[object] = []
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.add_learning(repo="o/r", instruction="Prefer bind parameters for raw SQL.")

    async def fake_generator(*_args: object, **kwargs: object) -> PullRequestAnswer:
        captured.append(kwargs.get("pr_history"))
        return PullRequestAnswer(answer="Use the local learning.")

    await run_ask(
        settings,
        number=42,
        question="Any repo-specific guidance?",
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=_empty_context_loader,
    )

    assert captured
    history = captured[0]
    assert history.learnings[0].instruction == "Prefer bind parameters for raw SQL."


async def test_run_ask_rejects_empty_question(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(ValueError, match="question must not be empty"):
        await run_ask(
            settings,
            number=42,
            question="   ",
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
            context_loader=_empty_context_loader,
        )


def test_render_answer_prints_sections() -> None:
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
        "question": "What changed?",
        "answer": {
            "answer": "Search now accepts a query.",
            "evidence": [
                {
                    "source": "changed_lines",
                    "file": "src/search.py",
                    "line": 1,
                    "detail": "The signature now includes query.",
                }
            ],
            "uncertainty": ["No callers are shown."],
            "follow_up_checks": ["Run search tests."],
        },
    }
    out = io.StringIO()

    render_answer(summary, out)

    text = out.getvalue()
    assert "PR #42 on o/r" in text
    assert "Context:      loaded" in text
    assert "Question:" in text
    assert "Answer:" in text
    assert "Evidence:" in text
    assert "[changed_lines] src/search.py:1" in text
    assert "Uncertainty:" in text
    assert "Follow-up checks:" in text
