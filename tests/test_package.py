"""Smoke tests for the package and CLI wiring."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from cli.main import __version__, app

_RUNNER = CliRunner()


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_cli_help_lists_known_commands() -> None:
    result = _RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "start", "stop", "index", "review"):
        assert command in result.stdout


def test_cli_version_flag_prints_version() -> None:
    result = _RUNNER.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
