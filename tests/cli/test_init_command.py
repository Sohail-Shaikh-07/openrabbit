"""Tests for ``cli.commands.init`` and the wired ``init`` CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.commands.init import InitConflict, run_init
from cli.exit_codes import NOT_IMPLEMENTED, OK, USER_ERROR
from cli.main import app
from cli.templates import TEMPLATES

_RUNNER = CliRunner()


def test_run_init_creates_all_templates(tmp_path: Path) -> None:
    result = run_init(tmp_path)

    scaffold = tmp_path / ".codereviewer"
    assert scaffold.is_dir()
    assert result.scaffold_dir == scaffold
    assert {p.name for p in result.created} == set(TEMPLATES)
    assert result.overwritten == []
    for name, content in TEMPLATES.items():
        assert (scaffold / name).read_text(encoding="utf-8") == content


def test_run_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    run_init(tmp_path)

    with pytest.raises(InitConflict) as exc:
        run_init(tmp_path)

    assert {p.name for p in exc.value.conflicts} == set(TEMPLATES)


def test_run_init_force_overwrites_existing_files(tmp_path: Path) -> None:
    run_init(tmp_path)
    (tmp_path / ".codereviewer" / "config.yml").write_text("trash", encoding="utf-8")

    result = run_init(tmp_path, force=True)

    assert {p.name for p in result.overwritten} == set(TEMPLATES)
    assert result.created == []
    assert (tmp_path / ".codereviewer" / "config.yml").read_text(encoding="utf-8") == TEMPLATES[
        "config.yml"
    ]


def test_run_init_missing_target_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        run_init(missing)


def test_run_init_file_target_raises(tmp_path: Path) -> None:
    file_target = tmp_path / "regular.txt"
    file_target.write_text("hi", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        run_init(file_target)


def test_cli_init_creates_scaffold(tmp_path: Path) -> None:
    result = _RUNNER.invoke(app, ["init", "--path", str(tmp_path)])

    assert result.exit_code == OK, result.stdout
    assert (tmp_path / ".codereviewer" / "config.yml").exists()


def test_cli_init_refuses_overwrite_without_force(tmp_path: Path) -> None:
    _RUNNER.invoke(app, ["init", "--path", str(tmp_path)])
    second = _RUNNER.invoke(app, ["init", "--path", str(tmp_path)])

    assert second.exit_code == USER_ERROR


def test_cli_init_force_overwrites(tmp_path: Path) -> None:
    _RUNNER.invoke(app, ["init", "--path", str(tmp_path)])
    (tmp_path / ".codereviewer" / "config.yml").write_text("trash", encoding="utf-8")

    result = _RUNNER.invoke(app, ["init", "--path", str(tmp_path), "--force"])

    assert result.exit_code == OK
    assert (tmp_path / ".codereviewer" / "config.yml").read_text(encoding="utf-8") == TEMPLATES[
        "config.yml"
    ]


def test_cli_quiet_and_verbose_mutually_exclusive() -> None:
    result = _RUNNER.invoke(app, ["--quiet", "--verbose", "init", "--path", "."])
    assert result.exit_code == USER_ERROR


@pytest.mark.parametrize("command", ["stop"])
def test_unimplemented_commands_exit_with_not_implemented(command: str) -> None:
    """`stop` still belongs to a later phase; `index` is now implemented."""
    result = _RUNNER.invoke(app, [command])
    assert result.exit_code == NOT_IMPLEMENTED


def test_start_without_config_exits_user_error(tmp_path: Path) -> None:
    """`start` should fail loudly if no scaffold exists at the workspace."""
    result = _RUNNER.invoke(app, ["start", "--workspace", str(tmp_path)])
    assert result.exit_code == USER_ERROR


def test_review_without_config_exits_user_error(tmp_path: Path) -> None:
    """`review` requires a scaffold too."""
    result = _RUNNER.invoke(app, ["review", "--pr", "42", "--workspace", str(tmp_path)])
    assert result.exit_code == USER_ERROR
