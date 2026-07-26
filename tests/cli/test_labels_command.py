"""Tests for ``cli.commands.labels``."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from agents.models import Finding, Severity
from cli.commands.labels import (
    render_label_proposals,
    render_label_proposals_json,
    run_label_proposals,
)
from cli.main import app
from configs import load_settings
from memory.store import SQLitePullRequestMemory

_BASE = "https://api.github.com"
_RUNNER = CliRunner()


def _pr_json() -> dict[str, object]:
    return {
        "number": 42,
        "title": "Fix admin export auth",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "fix/export-auth", "sha": "abcdef0123456789" + "0" * 24, "label": "a:feat"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "labels": [{"name": "bug"}],
        "body": "Fixes #12. Adds CLI tests for export auth.",
        "merged": False,
    }


def _mock_pr() -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/cli/main.py",
                    "status": "modified",
                    "additions": 2,
                    "deletions": 1,
                    "changes": 3,
                    "patch": "@@ -1,1 +1,2 @@\n-old\n+new\n",
                },
                {
                    "filename": "tests/cli/test_export_command.py",
                    "status": "added",
                    "additions": 10,
                    "deletions": 0,
                    "changes": 10,
                    "patch": "@@ -0,0 +1,2 @@\n+def test_export():\n+    pass\n",
                },
                {
                    "filename": "docs/export.md",
                    "status": "modified",
                    "additions": 4,
                    "deletions": 1,
                    "changes": 5,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                },
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(
            200,
            json=[{"sha": "c" * 40, "commit": {"message": "add CLI tests"}}],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/issues/12").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 12,
                "title": "Admin exports need authorization",
                "state": "open",
                "body": "The export endpoint needs security coverage.",
                "labels": [{"name": "security"}, {"name": "api"}],
                "html_url": "https://github.com/o/r/issues/12",
            },
        )
    )


def _mock_repo_labels() -> None:
    respx.get(f"{_BASE}/repos/o/r/labels").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "bug"},
                {"name": "security"},
                {"name": "api"},
                {"name": "tests"},
                {"name": "cli"},
                {"name": "documentation"},
            ],
        )
    )


@respx.mock
async def test_run_label_proposals_returns_read_only_suggestions(scaffold_repo: Path) -> None:
    _mock_pr()
    _mock_repo_labels()
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
                file="src/cli/main.py",
                line=1,
                confidence=0.9,
                title="Missing auth",
                reason="Exports need admin authorization.",
                suggestion="Require admin auth before exporting.",
            )
        ],
        context_loaded=True,
        comments_posted=False,
    )

    summary = await run_label_proposals(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
    )

    names = [item["name"] for item in summary["label_proposals"]]
    assert summary["schema_version"] == "1.0"
    assert summary["command"] == "labels"
    assert summary["repo"] == "o/r"
    assert summary["current_labels"] == ["bug"]
    assert summary["repository_labels_loaded"] is True
    assert summary["repository_labels_count"] == 6
    assert summary["linked_issue_count"] == 1
    assert summary["memory_enabled"] is True
    assert summary["conversation_count"] == 0
    assert summary["mutates_github"] is False
    assert "bug" not in names
    assert names[:3] == ["security", "tests", "cli"]
    exists_by_name = {
        item["name"]: item["exists_in_repository"] for item in summary["label_proposals"]
    }
    assert exists_by_name["security"] is True
    assert exists_by_name["tests"] is True
    assert exists_by_name["cli"] is True
    assert any(value is False for value in exists_by_name.values())


@respx.mock
async def test_run_label_proposals_fails_open_when_repository_labels_unavailable(
    scaffold_repo: Path,
) -> None:
    _mock_pr()
    respx.get(f"{_BASE}/repos/o/r/labels").mock(return_value=httpx.Response(404, text="missing"))
    settings = load_settings(scaffold_repo, env={})

    summary = await run_label_proposals(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        limit=2,
    )

    assert summary["repository_labels_loaded"] is False
    assert summary["repository_labels_count"] == 0
    assert summary["proposal_count"] == 2
    assert all(item["exists_in_repository"] is None for item in summary["label_proposals"])


def test_render_label_proposals_prints_sections() -> None:
    summary = {
        "repo": "o/r",
        "number": 42,
        "title": "Fix admin export auth",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 2,
        "binary_files": 0,
        "hunks": 2,
        "commits": 1,
        "current_labels": ["bug"],
        "proposal_count": 1,
        "label_proposals": [
            {
                "name": "security",
                "confidence": 0.92,
                "reason": "Linked issue has this label.",
                "signals": ["linked_issue", "review_memory"],
                "exists_in_repository": True,
            }
        ],
    }
    out = io.StringIO()

    render_label_proposals(summary, out)

    text = out.getvalue()
    assert "PR #42 on o/r" in text
    assert "Existing:     bug" in text
    assert "GitHub write: no" in text
    assert "Label proposals:" in text
    assert "security (0.92)" in text


def test_render_label_proposals_json_prints_deterministic_summary() -> None:
    summary = {
        "schema_version": "1.0",
        "command": "labels",
        "repo": "o/r",
        "number": 42,
        "label_proposals": [],
    }
    out = io.StringIO()

    render_label_proposals_json(summary, out)

    text = out.getvalue()
    assert text.endswith("\n")
    assert '"command": "labels"' in text
    assert '"schema_version": "1.0"' in text


def test_cli_labels_accepts_flags(scaffold_repo: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "labels",
            "--pr",
            "42",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "o/r",
            "--format",
            "json",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code != 2
