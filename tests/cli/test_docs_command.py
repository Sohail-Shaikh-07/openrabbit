"""Tests for ``cli.commands.docs``."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from cli.commands.docs import (
    render_docs_suggestions,
    render_docs_suggestions_json,
    run_docs_suggestions,
)
from cli.main import app
from configs import load_settings

_BASE = "https://api.github.com"
_RUNNER = CliRunner()


def _pr_json() -> dict[str, object]:
    return {
        "number": 42,
        "title": "Add changelog and docs commands",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "feature/docs", "sha": "abcdef0123456789" + "0" * 24, "label": "a:feat"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "labels": [],
        "body": "Adds public CLI behavior.",
        "merged": False,
    }


def _mock_pr() -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/42").mock(return_value=httpx.Response(200, json=_pr_json()))
    respx.get(f"{_BASE}/repos/o/r/pulls/42/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/cli/commands/changelog.py",
                    "status": "added",
                    "additions": 30,
                    "deletions": 0,
                    "changes": 30,
                    "patch": (
                        "@@ -0,0 +1,5 @@\n"
                        "+def run_changelog_draft():\n"
                        "+    pass\n"
                        "+class ChangelogEntry:\n"
                        "+    pass\n"
                    ),
                },
                {
                    "filename": "examples/github-actions/openrabbit-interactive.yml",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 1,
                    "changes": 4,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                },
                {
                    "filename": "app/internal.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                    "changes": 2,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
                },
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/42/commits").mock(
        return_value=httpx.Response(
            200,
            json=[{"sha": "c" * 40, "commit": {"message": "add docs command"}}],
        )
    )


@respx.mock
async def test_run_docs_suggestions_returns_read_only_suggestions(
    scaffold_repo: Path,
) -> None:
    _mock_pr()
    settings = load_settings(scaffold_repo, env={})

    summary = await run_docs_suggestions(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
    )

    assert summary["schema_version"] == "1.0"
    assert summary["command"] == "docs"
    assert summary["repo"] == "o/r"
    assert summary["number"] == 42
    assert summary["changed_public_surface_count"] == 2
    assert summary["mutates_files"] is False
    assert summary["mutates_github"] is False
    assert summary["workflow_controls"]["mode"] == "read_only"
    assert summary["workflow_controls"]["required_permissions"] == ["pull_requests:read"]
    assert summary["workflow_controls"]["files"]["status"] == "not_supported"
    assert summary["workflow_controls"]["managed_comment"]["status"] == "not_supported"

    suggestions = summary["docs_suggestions"]
    target_paths = [item["target_path"] for item in suggestions]
    assert target_paths[:2] == ["README.md", "docs/interactive-pr-workflows.md"]
    assert "docs/github-actions.md" in target_paths
    readme = next(item for item in suggestions if item["target_path"] == "README.md")
    assert "src/cli/commands/changelog.py" in readme["source_paths"]
    assert "run_changelog_draft" in readme["public_symbols"]
    assert "ChangelogEntry" in readme["public_symbols"]


@respx.mock
async def test_run_docs_suggestions_respects_limit(scaffold_repo: Path) -> None:
    _mock_pr()
    settings = load_settings(scaffold_repo, env={})

    summary = await run_docs_suggestions(
        settings,
        number=42,
        repo="o/r",
        env={"GITHUB_TOKEN": "tkn"},
        limit=1,
    )

    assert summary["suggestion_count"] == 1
    assert len(summary["docs_suggestions"]) == 1


def test_render_docs_suggestions_prints_sections() -> None:
    summary = {
        "repo": "o/r",
        "number": 42,
        "title": "Add changelog command",
        "files_changed": 1,
        "suggestion_count": 1,
        "docs_suggestions": [
            {
                "target_path": "README.md",
                "category": "cli",
                "confidence": 0.94,
                "reason": "CLI command behavior changed.",
                "source_paths": ["src/cli/main.py"],
                "public_symbols": ["docs_command"],
            }
        ],
    }
    out = io.StringIO()

    render_docs_suggestions(summary, out)

    text = out.getvalue()
    assert "Documentation suggestions for PR #42 on o/r" in text
    assert "GitHub write: no" in text
    assert "File write:   no" in text
    assert "README.md (cli, 0.94)" in text
    assert "docs_command" in text


def test_render_docs_suggestions_json_prints_deterministic_summary() -> None:
    summary = {
        "schema_version": "1.0",
        "command": "docs",
        "repo": "o/r",
        "docs_suggestions": [],
        "mutates_files": False,
        "mutates_github": False,
    }
    out = io.StringIO()

    render_docs_suggestions_json(summary, out)

    text = out.getvalue()
    assert text.endswith("\n")
    assert '"command": "docs"' in text
    assert '"schema_version": "1.0"' in text


def test_cli_docs_accepts_flags(scaffold_repo: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "docs",
            "--pr",
            "42",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "o/r",
            "--limit",
            "5",
            "--format",
            "json",
        ],
    )

    assert result.exit_code != 2
