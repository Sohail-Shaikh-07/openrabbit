"""Tests for the local memory inspection command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents.models import Finding, Severity
from cli.commands.memory import (
    render_memory_summary,
    run_memory_export,
    run_memory_inspect,
    run_memory_prune,
)
from cli.main import app
from configs import load_settings
from memory.store import SQLitePullRequestMemory

runner = CliRunner()


def _finding() -> Finding:
    return Finding(
        severity=Severity.high,
        category="security",
        file="app/repositories/task_repository.py",
        line=74,
        confidence=0.9,
        title="SQL Injection vulnerability",
        reason="Raw SQL is built from user input.",
        suggestion="Use bind parameters.",
    )


def test_run_memory_inspect_reports_missing_database(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    memory_path = settings.resolved_memory_path()

    summary = run_memory_inspect(settings, repo="owner/repo", pr_number=7)

    assert summary["memory_database_exists"] is False
    assert summary["findings_count"] == 0
    assert not memory_path.exists()


def test_run_memory_inspect_loads_stored_findings(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )

    summary = run_memory_inspect(settings, repo="owner/repo", pr_number=7)

    assert summary["memory_database_exists"] is True
    assert summary["last_reviewed_sha"] == "abc123"
    assert summary["findings_count"] == 1
    assert summary["status_counts"] == {"new": 1}
    assert summary["findings"][0]["title"] == "SQL Injection vulnerability"  # type: ignore[index]


def test_render_memory_summary_includes_statuses(scaffold_repo: Path, tmp_path: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )
    summary = run_memory_inspect(settings, repo="owner/repo", pr_number=7)
    output = tmp_path / "memory.txt"

    with output.open("w", encoding="utf-8") as out:
        render_memory_summary(summary, out)

    text = output.read_text(encoding="utf-8")
    assert "OpenRabbit memory" in text
    assert "Statuses:    new:1" in text
    assert "SQL Injection vulnerability" in text


def test_run_memory_export_writes_repository_json(scaffold_repo: Path, tmp_path: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )
    output = tmp_path / "memory.json"

    summary = run_memory_export(settings, repo="owner/repo", output=output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert summary["output_path"] == str(output)
    assert summary["review_runs"] == 1
    assert summary["findings"] == 1
    assert payload["repo"] == "owner/repo"
    assert payload["findings"][0]["title"] == "SQL Injection vulnerability"
    assert "payload_json" not in payload["findings"][0]


def test_run_memory_prune_deletes_old_memory(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )

    summary = run_memory_prune(settings, repo="owner/repo", prune_before="2999-01-01")
    payload = store.export_repo("owner/repo")

    assert summary["deleted"] == {"review_runs": 1, "findings": 1}
    assert payload["review_runs"] == []
    assert payload["findings"] == []


def test_memory_cli_prints_json_for_pr_inspect(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )

    result = runner.invoke(
        app,
        [
            "memory",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "owner/repo",
            "--pr",
            "7",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["repo"] == "owner/repo"
    assert payload["findings_count"] == 1


def test_memory_cli_exports_json(scaffold_repo: Path, tmp_path: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )
    output = tmp_path / "memory.json"

    result = runner.invoke(
        app,
        [
            "memory",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "owner/repo",
            "--export",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert output.is_file()
    assert "OpenRabbit memory export" in result.output


def test_memory_cli_prunes_with_json_summary(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.record_review(
        repo="owner/repo",
        pr_number=7,
        head_sha="abc123",
        findings=[_finding()],
        context_loaded=True,
        comments_posted=False,
    )

    result = runner.invoke(
        app,
        [
            "memory",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "owner/repo",
            "--prune-before",
            "2999-01-01",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["deleted"] == {"findings": 1, "review_runs": 1}


def test_memory_cli_rejects_ambiguous_export_and_prune(scaffold_repo: Path) -> None:
    result = runner.invoke(
        app,
        [
            "memory",
            "--workspace",
            str(scaffold_repo),
            "--repo",
            "owner/repo",
            "--export",
            "memory.json",
            "--prune-before",
            "2999-01-01",
        ],
    )

    assert result.exit_code != 0
    assert "must be run separately" in result.output


def test_memory_cli_command_exists() -> None:
    result = runner.invoke(app, ["memory", "--help"])

    assert result.exit_code == 0
    assert "Inspect local OpenRabbit memory" in result.output
