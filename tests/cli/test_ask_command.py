"""Tests for ``cli.commands.ask``."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import pytest
import respx

from agents.prompting import format_prompt_diff
from cli.commands.ask import (
    AnswerEvidence,
    PullRequestAnswer,
    render_answer,
    render_answer_json,
    render_answer_markdown,
    run_ask,
)
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
                }
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
async def test_run_ask_uses_prepared_controls_for_context_and_model(scaffold_repo: Path) -> None:
    _mock_controlled_pr()
    context_payloads: list[object] = []
    generator_payloads: list[object] = []

    async def capture_context(payload: object) -> None:
        context_payloads.append(payload)
        return None

    async def fake_generator(*args: object, **_kwargs: object) -> PullRequestAnswer:
        generator_payloads.append(args[0])
        paths = [file_.path for file_ in args[0].files]
        return PullRequestAnswer(answer=", ".join(paths))

    settings = load_settings(scaffold_repo, env={})
    _enable_ast_controls(settings)

    summary = await run_ask(
        settings,
        number=42,
        question="What changed?",
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        generator=fake_generator,
        context_loader=capture_context,
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
    assert summary["answer"]["answer"] == "src/search.py"


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


def test_render_answer_markdown_prints_report_sections() -> None:
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

    render_answer_markdown(summary, out)

    text = out.getvalue()
    assert "# PR #42 Ask" in text
    assert "## Question" in text
    assert "## Evidence" in text
    assert "- `changed_lines` `src/search.py:1`: The signature now includes query." in text


def test_render_answer_json_prints_deterministic_summary() -> None:
    summary = {
        "repo": "o/r",
        "number": 42,
        "question": "What changed?",
        "answer": {"answer": "Search now accepts a query."},
    }
    out = io.StringIO()

    render_answer_json(summary, out)

    text = out.getvalue()
    assert text.endswith("\n")
    assert '"answer": {' in text
    assert '"question": "What changed?"' in text
