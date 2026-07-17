"""Tests for CLI polish additions (OP-34).

Covers:
- ``openrabbit review --dry-run`` flag accepted and returned in summary
- ``render_summary`` includes a DRY RUN banner when dry_run=True
- ``openrabbit start`` banner output (version, repo, interval)
- ``openrabbit --version`` output format
"""

from __future__ import annotations

import io
import re

from typer.testing import CliRunner

from cli.commands.review import render_summary
from cli.main import __version__, app

runner = CliRunner()


def _plain_help(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text).replace(" ", "")


# ---------------------------------------------------------------------------
# render_summary dry_run banner
# ---------------------------------------------------------------------------


def test_render_summary_dry_run_banner() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 3,
        "binary_files": 0,
        "hunks": 2,
        "commits": 1,
        "dry_run": True,
    }
    out = io.StringIO()
    render_summary(summary, out)
    text = out.getvalue()
    assert "DRY RUN" in text


def test_render_summary_no_dry_run_banner_by_default() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 3,
        "binary_files": 0,
        "hunks": 2,
        "commits": 1,
    }
    out = io.StringIO()
    render_summary(summary, out)
    text = out.getvalue()
    assert "DRY RUN" not in text


def test_render_summary_dry_run_false_no_banner() -> None:
    summary = {
        "repo": "o/r",
        "number": 7,
        "title": "Hello",
        "state": "open",
        "head_sha": "abcdef012345",
        "files_changed": 3,
        "binary_files": 0,
        "hunks": 2,
        "commits": 1,
        "dry_run": False,
    }
    out = io.StringIO()
    render_summary(summary, out)
    text = out.getvalue()
    assert "DRY RUN" not in text


# ---------------------------------------------------------------------------
# review --dry-run CLI flag
# ---------------------------------------------------------------------------


def test_review_command_accepts_dry_run_flag() -> None:
    result = runner.invoke(app, ["review", "--help"])
    # Strip ANSI escape codes before checking (CI terminal adds colour).
    import re

    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "dry-run" in plain


def test_describe_command_is_listed() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "describe" in result.output


def test_describe_command_accepts_format_option() -> None:
    result = runner.invoke(app, ["describe", "--help"])
    assert result.exit_code == 0
    assert "--format" in _plain_help(result.output)


def test_improve_command_is_listed() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "improve" in result.output


def test_ask_command_is_listed() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ask" in result.output


def test_ask_command_accepts_format_option() -> None:
    result = runner.invoke(app, ["ask", "--help"])
    assert result.exit_code == 0
    assert "--format" in _plain_help(result.output)


def test_start_command_accepts_once_option() -> None:
    result = runner.invoke(app, ["start", "--help"])
    assert result.exit_code == 0
    assert "--once" in _plain_help(result.output)


def test_stop_command_accepts_workspace_option() -> None:
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0
    assert "--workspace" in _plain_help(result.output)


# ---------------------------------------------------------------------------
# openrabbit --version
# ---------------------------------------------------------------------------


def test_version_flag_output() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "openrabbit" in result.output
    assert __version__ in result.output


# ---------------------------------------------------------------------------
# start banner (format_start_banner helper)
# ---------------------------------------------------------------------------


def test_format_start_banner_contains_version() -> None:
    from cli.commands.start import format_start_banner

    banner = format_start_banner(repo="owner/repo", interval=30, ver=__version__)
    assert __version__ in banner


def test_format_start_banner_contains_repo() -> None:
    from cli.commands.start import format_start_banner

    banner = format_start_banner(repo="owner/my-repo", interval=30, ver="1.0.0")
    assert "owner/my-repo" in banner


def test_format_start_banner_contains_interval() -> None:
    from cli.commands.start import format_start_banner

    banner = format_start_banner(repo="o/r", interval=60, ver="1.0.0")
    assert "60" in banner
