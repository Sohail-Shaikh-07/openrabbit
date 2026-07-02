"""Tests for ``cli.commands.review``."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import respx

from agents.models import Finding, Severity
from cli.commands.review import render_summary, run_review
from cli.commands.review_pipeline import ReviewPipelineResult
from configs import load_settings
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
    )

    assert summary["findings_count"] == 1
    assert summary["dropped_findings_count"] == 2
    assert summary["findings"][0]["title"] == "Missing guard"
    assert summary["findings"][0]["score"] == 2.7


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
        "findings": [],
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
    assert "[HIGH] Missing guard" in text
    assert "src/a.py:2" in text
