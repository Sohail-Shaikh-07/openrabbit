"""Tests for ``cli.commands.similar_issues``."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from agents.models import Finding, Severity
from cli.commands.similar_issues import (
    render_similar_issues,
    render_similar_issues_json,
    run_similar_issues,
)
from cli.main import app
from configs import load_settings
from memory.store import SQLitePullRequestMemory

_BASE = "https://api.github.com"
_RUNNER = CliRunner()


def _pr_json(*, body: str = "Fixes #12. Harden admin export auth.") -> dict[str, object]:
    return {
        "number": 42,
        "title": "Fix admin export auth",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "fix/export-auth", "sha": "abcdef0123456789" + "0" * 24, "label": "a:fix"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "labels": [{"name": "security"}],
        "body": body,
        "merged": False,
    }


def _mock_pr(
    *,
    body: str = "Fixes #12. Harden admin export auth.",
    issue_body: str = "The export endpoint needs admin security coverage.",
) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(
        return_value=httpx.Response(200, json=_pr_json(body=body))
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/api/export_auth.py",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 1,
                    "changes": 4,
                    "patch": "@@ -1,1 +1,2 @@\n-old\n+new\n",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(
            200,
            json=[{"sha": "c" * 40, "commit": {"message": "fix admin export auth"}}],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/issues/12").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 12,
                "title": "Admin exports need authorization",
                "state": "open",
                "body": issue_body,
                "labels": [{"name": "security"}, {"name": "api"}],
                "html_url": "https://github.com/o/r/issues/12",
            },
        )
    )


def _mock_issue_search(
    status_code: int = 200,
    *,
    result_body: str = "Admin export access must reject invalid user tokens.",
) -> dict[str, str]:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.url.params["q"]
        if status_code >= 400:
            return httpx.Response(status_code, text="search unavailable")
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "items": [
                    {
                        "number": 12,
                        "title": "Admin exports need authorization",
                        "state": "open",
                        "body": "The export endpoint needs admin security coverage.",
                        "labels": [{"name": "security"}, {"name": "api"}],
                        "html_url": "https://github.com/o/r/issues/12",
                    },
                    {
                        "number": 22,
                        "title": "Export auth should reject non-admin users",
                        "state": "closed",
                        "body": result_body,
                        "labels": [{"name": "security"}],
                        "html_url": "https://github.com/o/r/issues/22",
                    },
                ],
            },
        )

    respx.get(f"{_BASE}/search/issues").mock(side_effect=handler)
    return captured


@respx.mock
async def test_run_similar_issues_combines_linked_search_and_memory(
    scaffold_repo: Path,
) -> None:
    _mock_pr()
    captured = _mock_issue_search()
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="o/r",
        pr_number=42,
        head_sha="previous-sha",
        findings=[
            Finding(
                severity=Severity.high,
                category="security",
                file="src/api/export_auth.py",
                line=3,
                confidence=0.9,
                title="Missing auth",
                reason="Exports need admin authorization.",
                suggestion="Require admin auth before exporting.",
            )
        ],
        context_loaded=True,
        comments_posted=False,
    )

    summary = await run_similar_issues(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
    )

    assert summary["schema_version"] == "1.0"
    assert summary["command"] == "similar-issues"
    assert summary["repo"] == "o/r"
    assert summary["linked_issue_count"] == 1
    assert summary["memory_enabled"] is True
    assert summary["search_results_loaded"] is True
    assert "repo:o/r is:issue" in captured["q"]
    assert "#12" not in captured["q"]
    assert summary["mutates_files"] is False
    assert summary["mutates_github"] is False
    assert summary["workflow_controls"]["mode"] == "read_only"
    assert summary["workflow_controls"]["required_permissions"] == [
        "pull_requests:read",
        "issues:read",
    ]
    assert summary["workflow_controls"]["github"]["requested"] is False
    assert summary["workflow_controls"]["managed_comment"]["status"] == "not_supported"

    results = summary["issue_results"]
    assert [item["number"] for item in results] == [12, 22]
    assert results[0]["score"] == 1.0
    assert "linked_issue" in results[0]["source_signals"]
    assert "github_issue_search" in results[0]["source_signals"]
    assert "memory_category" in results[1]["source_signals"]


@respx.mock
async def test_run_similar_issues_fails_open_when_search_unavailable(
    scaffold_repo: Path,
) -> None:
    _mock_pr()
    _mock_issue_search(status_code=503)
    settings = load_settings(scaffold_repo, env={})

    summary = await run_similar_issues(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
    )

    assert summary["search_results_loaded"] is False
    assert "GitHub API error 503" in summary["search_error"]
    assert [item["number"] for item in summary["issue_results"]] == [12]
    assert summary["issue_results"][0]["source_signals"] == ["linked_issue"]


@respx.mock
async def test_run_similar_issues_redacts_secrets_from_query_and_results(
    scaffold_repo: Path,
) -> None:
    secret = "ghp_secret1234567890abcdef"
    _mock_pr(
        body=f"Fixes #12. Rotate GITHUB_TOKEN={secret} before export auth.",
        issue_body=f"Linked issue includes token={secret} in pasted logs.",
    )
    captured = _mock_issue_search(
        result_body=f"Search result body includes authorization={secret} in logs."
    )
    settings = load_settings(scaffold_repo, env={})

    summary = await run_similar_issues(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
    )

    payload = json.dumps(summary, sort_keys=True)
    assert secret not in captured["q"]
    assert "GITHUB_TOKEN=" not in captured["q"]
    assert "token=[REDACTED]" not in captured["q"]
    assert secret not in payload
    assert "GITHUB_TOKEN=" not in payload
    assert "authorization=[REDACTED]" in payload
    assert summary["mutates_github"] is False


def test_render_similar_issues_prints_sections() -> None:
    summary = {
        "repo": "o/r",
        "number": 42,
        "title": "Fix admin export auth",
        "linked_issue_count": 1,
        "result_count": 1,
        "search_results_loaded": True,
        "search_query": "admin export auth in:title,body",
        "issue_results": [
            {
                "number": 12,
                "title": "Admin exports need authorization",
                "state": "open",
                "score": 1.0,
                "labels": ["security"],
                "source_signals": ["linked_issue", "metadata_overlap"],
                "reason": "PR explicitly references issue #12.",
            }
        ],
    }
    out = io.StringIO()

    render_similar_issues(summary, out)

    text = out.getvalue()
    assert "Similar issues for PR #42 on o/r" in text
    assert "GitHub write: no" in text
    assert "#12 Admin exports need authorization" in text
    assert "linked_issue" in text


def test_render_similar_issues_json_prints_deterministic_summary() -> None:
    summary = {
        "schema_version": "1.0",
        "command": "similar-issues",
        "repo": "o/r",
        "issue_results": [],
        "mutates_github": False,
    }
    out = io.StringIO()

    render_similar_issues_json(summary, out)

    payload = json.loads(out.getvalue())
    assert payload["command"] == "similar-issues"
    assert payload["schema_version"] == "1.0"


def test_cli_similar_issues_accepts_flags(scaffold_repo: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "similar-issues",
            "--pr",
            "42",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "o/r",
            "--limit",
            "5",
            "--search-limit",
            "10",
            "--format",
            "json",
        ],
    )

    assert result.exit_code != 2
