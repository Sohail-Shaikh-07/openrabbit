"""Tests for ``cli.commands.changelog``."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from cli.commands.changelog import (
    render_changelog_draft,
    render_changelog_draft_json,
    run_changelog_draft,
)
from cli.main import app
from configs import load_settings

_BASE = "https://api.github.com"
_RUNNER = CliRunner()


def _pr_json(
    number: int,
    title: str,
    *,
    labels: list[str],
    merged_at: str | None,
    author: str = "alice",
) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "state": "closed",
        "draft": False,
        "user": {"login": author, "id": number},
        "head": {"ref": f"feature/{number}", "sha": "a" * 40, "label": f"{author}:head"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": merged_at or "2026-07-03T00:00:00Z",
        "merged_at": merged_at,
        "html_url": f"https://github.com/o/r/pull/{number}",
        "labels": [{"name": label} for label in labels],
    }


@respx.mock
async def test_run_changelog_draft_groups_merged_prs_and_notes(
    scaffold_repo: Path,
) -> None:
    notes = scaffold_repo / "release-notes.md"
    notes.write_text(
        "# Release notes\n\nFocus on maintainers. token=super-secret-value\n",
        encoding="utf-8",
    )
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                _pr_json(
                    13,
                    "Add label apply command",
                    labels=["feature", "cli"],
                    merged_at="2026-07-26T10:00:00Z",
                ),
                _pr_json(
                    12,
                    "Fix release workflow",
                    labels=["bug"],
                    merged_at="2026-07-20T10:00:00Z",
                    author="bob",
                ),
                _pr_json(
                    11,
                    "Document workflows",
                    labels=["docs"],
                    merged_at="2026-07-10T10:00:00Z",
                ),
                _pr_json(
                    10,
                    "Old merged change",
                    labels=["enhancement"],
                    merged_at="2026-06-10T10:00:00Z",
                ),
                _pr_json(9, "Closed but not merged", labels=["bug"], merged_at=None),
            ],
        )
    )
    settings = load_settings(scaffold_repo, env={})

    summary = await run_changelog_draft(
        settings,
        repo="o/r",
        workspace=scaffold_repo,
        since="2026-07-01",
        until="2026-07-31",
        limit=10,
        scan_limit=10,
        notes=[Path("release-notes.md")],
        env={"GITHUB_TOKEN": "tkn"},
    )

    assert summary["schema_version"] == "1.0"
    assert summary["command"] == "changelog"
    assert summary["repo"] == "o/r"
    assert summary["source_pr_count"] == 5
    assert summary["merged_pr_count"] == 3
    assert summary["mutates_files"] is False
    assert summary["mutates_github"] is False
    assert summary["workflow_controls"]["mode"] == "read_only"
    assert summary["workflow_controls"]["dry_run"] is True
    assert summary["workflow_controls"]["required_permissions"] == ["pull_requests:read"]
    assert summary["workflow_controls"]["github"]["mutates"] is False
    assert summary["workflow_controls"]["files"]["mutates"] is False
    assert summary["workflow_controls"]["managed_comment"]["status"] == "not_supported"
    assert summary["notes_loaded"] == 1
    assert "super-secret-value" not in str(summary["notes"])
    assert "token=[REDACTED]" in summary["notes"][0]["excerpt"]

    sections = {section["name"]: section["entries"] for section in summary["sections"]}
    assert [entry["number"] for entry in sections["Features"]] == [13]
    assert [entry["number"] for entry in sections["Fixes"]] == [12]
    assert [entry["number"] for entry in sections["Documentation"]] == [11]
    assert sections["Features"][0]["labels"] == ["cli", "feature"]


@respx.mock
async def test_run_changelog_draft_respects_limit(scaffold_repo: Path) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                _pr_json(3, "Newest", labels=["feature"], merged_at="2026-07-03T00:00:00Z"),
                _pr_json(2, "Middle", labels=["bug"], merged_at="2026-07-02T00:00:00Z"),
                _pr_json(1, "Oldest", labels=[], merged_at="2026-07-01T00:00:00Z"),
            ],
        )
    )
    settings = load_settings(scaffold_repo, env={})

    summary = await run_changelog_draft(
        settings,
        repo="o/r",
        workspace=scaffold_repo,
        limit=2,
        scan_limit=3,
        env={"GITHUB_TOKEN": "tkn"},
    )

    section_numbers = [
        entry["number"] for section in summary["sections"] for entry in section["entries"]
    ]
    assert section_numbers == [3, 2]
    assert summary["merged_pr_count"] == 2


async def test_run_changelog_draft_rejects_invalid_range(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(ValueError, match="--since"):
        await run_changelog_draft(
            settings,
            repo="o/r",
            workspace=scaffold_repo,
            since="2026-08-01",
            until="2026-07-01",
            env={"GITHUB_TOKEN": "tkn"},
        )


def test_render_changelog_draft_prints_sections() -> None:
    summary = {
        "repo": "o/r",
        "since": "2026-07-01",
        "until": "2026-07-31",
        "merged_pr_count": 1,
        "notes": [{"path": "docs/release.md", "truncated": True}],
        "sections": [
            {
                "name": "Features",
                "entries": [
                    {
                        "number": 13,
                        "title": "Add label apply command",
                        "labels": ["cli", "feature"],
                    }
                ],
            }
        ],
    }
    out = io.StringIO()

    render_changelog_draft(summary, out)

    text = out.getvalue()
    assert "Changelog draft for o/r" in text
    assert "GitHub write: no" in text
    assert "File write:   no" in text
    assert "docs/release.md (truncated)" in text
    assert "## Features" in text
    assert "Add label apply command (#13) [cli, feature]" in text


def test_render_changelog_draft_json_prints_deterministic_summary() -> None:
    summary = {
        "schema_version": "1.0",
        "command": "changelog",
        "repo": "o/r",
        "sections": [],
        "mutates_files": False,
        "mutates_github": False,
    }
    out = io.StringIO()

    render_changelog_draft_json(summary, out)

    text = out.getvalue()
    assert text.endswith("\n")
    assert '"command": "changelog"' in text
    assert '"schema_version": "1.0"' in text


def test_cli_changelog_accepts_flags(scaffold_repo: Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "changelog",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "o/r",
            "--since",
            "2026-07-01",
            "--until",
            "2026-07-31",
            "--limit",
            "3",
            "--scan-limit",
            "10",
            "--notes",
            "CHANGELOG.md",
            "--format",
            "json",
        ],
    )

    assert result.exit_code != 2
