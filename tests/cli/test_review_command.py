"""Tests for ``cli.commands.review``."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import respx

from agents.models import Finding, Severity
from cli.commands.review import render_summary, run_review
from cli.commands.review_pipeline import ReviewPipelineResult
from configs import load_settings
from github_ import GitHubAPIError
from memory.store import SQLitePullRequestMemory
from rag.retriever import RetrievalResult
from ranking.ranker import RankedFinding

_BASE = "https://api.github.com"


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

    async def fake_runner(*_args: object, **_kwargs: object) -> ReviewPipelineResult:
        return ReviewPipelineResult(
            agent_results=[],
            ranked_findings=[RankedFinding(finding=finding, score=2.7)],
            dropped_findings_count=2,
        )

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

    assert summary["findings_count"] == 1
    assert summary["dropped_findings_count"] == 2
    assert summary["findings"][0]["title"] == "Missing guard"
    assert summary["findings"][0]["score"] == 2.7
    assert summary["comments_posted"] is False
    assert summary["publish_status"] == "dry_run"


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
    assert first_history is not None
    assert second_history is not None
    assert first_history.local.last_reviewed_sha is None
    assert second_history.local.last_reviewed_sha == "abcdef0123456789" + "0" * 24
    assert first["memory_status_counts"] == {"new": 1}
    assert first["findings"][0]["memory_status"] == "new"
    assert second["memory_status_counts"] == {"still_present": 1}
    assert second["findings"][0]["memory_status"] == "still_present"


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
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(return_value=httpx.Response(200, json=[]))
    retrieval = RetrievalResult(security=[{"score": 0.9, "payload": {"name": "rule"}}])
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
        "publish_status": "no_findings",
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
