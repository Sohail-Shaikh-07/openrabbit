"""Tests for ``openrabbit connector-health``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.commands.connector_health import ConnectorHealthResult
from cli.main import app

_RUNNER = CliRunner()


def test_connector_health_cli_prints_disabled_defaults(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cli.main.run_connector_health_check",
        lambda _settings: [
            ConnectorHealthResult(
                name="mcp",
                enabled=False,
                available=False,
                source_kind="mcp",
                reason="disabled",
            )
        ],
    )

    result = _RUNNER.invoke(app, ["connector-health", "--workspace", str(scaffold_repo)])

    assert result.exit_code == 0
    assert "mcp" in result.output
    assert "disabled" in result.output


def test_connector_health_cli_exits_user_error_when_enabled_connector_unavailable(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cli.main.run_connector_health_check",
        lambda _settings: [
            ConnectorHealthResult(
                name="jira",
                enabled=True,
                available=False,
                source_kind="issue_tracker",
                reason="TEAM_JIRA_TOKEN is not set",
            )
        ],
    )

    result = _RUNNER.invoke(app, ["connector-health", "--workspace", str(scaffold_repo)])

    assert result.exit_code == 1
    assert "jira" in result.output
    assert "TEAM_JIRA_TOKEN is not set" in result.output
