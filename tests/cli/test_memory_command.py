"""Tests for the local memory inspection command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agents.models import Finding, Severity
from cli.commands.memory import render_memory_summary, run_memory_inspect
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


def test_memory_cli_command_exists() -> None:
    result = runner.invoke(app, ["memory", "--help"])

    assert result.exit_code == 0
    assert "Inspect local OpenRabbit memory" in result.output
