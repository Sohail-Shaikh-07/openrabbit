"""Smoke tests for the package and CLI wiring."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from cli.main import __version__, app

_RUNNER = CliRunner()
_ROOT = Path(__file__).resolve().parents[1]


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_cli_help_lists_known_commands() -> None:
    result = _RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "start", "stop", "index", "model-health", "review"):
        assert command in result.stdout


def test_cli_version_flag_prints_version() -> None:
    result = _RUNNER.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_pyproject_version_has_release_artifacts() -> None:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = pyproject["tool"]["poetry"]["version"]

    assert re.fullmatch(r"\d+\.\d+\.\d+", package_version)
    assert (_ROOT / "docs" / f"release-v{package_version}.md").exists()
    assert (_ROOT / "changelog" / f"v{package_version}.txt").exists()
    assert f"## v{package_version}" in (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
