"""Tests for the openrabbit install-model CLI command (OP-30)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

_MODEL_ID = "openrabbit/openrabbit-reviewer-v1"
_ADAPTER_FILES = ["adapter_model.safetensors", "adapter_config.json", "README.md"]


def _fake_snapshot(dest_dir: Path) -> None:
    """Simulate what huggingface_hub.snapshot_download writes to disk."""
    for name in _ADAPTER_FILES:
        (dest_dir / name).write_text(f"# fake {name}")


# ---------------------------------------------------------------------------
# run_install_model unit tests (command logic layer)
# ---------------------------------------------------------------------------


def test_install_model_creates_install_dir(tmp_path: Path) -> None:
    from cli.commands.install_model import run_install_model

    install_dir = tmp_path / "models"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        result = run_install_model(install_dir=install_dir)

    assert result.install_dir.is_dir()


def test_install_model_returns_install_result(tmp_path: Path) -> None:
    from cli.commands.install_model import InstallResult, run_install_model

    install_dir = tmp_path / "models"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        result = run_install_model(install_dir=install_dir)

    assert isinstance(result, InstallResult)


def test_install_model_result_model_id(tmp_path: Path) -> None:
    from cli.commands.install_model import run_install_model

    install_dir = tmp_path / "models"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        result = run_install_model(install_dir=install_dir)

    assert result.model_id == _MODEL_ID


def test_install_model_downloads_adapter_files(tmp_path: Path) -> None:
    from cli.commands.install_model import run_install_model

    install_dir = tmp_path / "models"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        result = run_install_model(install_dir=install_dir)

    for name in _ADAPTER_FILES:
        assert (result.install_dir / name).exists(), f"missing {name}"


def test_install_model_calls_snapshot_download(tmp_path: Path) -> None:
    from cli.commands.install_model import run_install_model

    install_dir = tmp_path / "models"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        run_install_model(install_dir=install_dir)

    mock_dl.assert_called_once()


def test_install_model_default_model_id(tmp_path: Path) -> None:
    from cli.commands.install_model import run_install_model

    install_dir = tmp_path / "models"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        result = run_install_model(install_dir=install_dir)

    assert result.model_id == _MODEL_ID


def test_install_model_custom_model_id(tmp_path: Path) -> None:
    from cli.commands.install_model import run_install_model

    install_dir = tmp_path / "models"
    custom = "myorg/custom-model"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        result = run_install_model(model_id=custom, install_dir=install_dir)

    assert result.model_id == custom


def test_install_model_verifies_adapter_config_exists(tmp_path: Path) -> None:
    from cli.commands.install_model import run_install_model

    install_dir = tmp_path / "models"

    def bad_download(*a: object, local_dir: Path, **kw: object) -> None:
        (local_dir / "adapter_model.safetensors").write_text("fake")
        # adapter_config.json is intentionally missing

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = bad_download
        with pytest.raises((FileNotFoundError, ValueError, RuntimeError)):
            run_install_model(install_dir=install_dir)


# ---------------------------------------------------------------------------
# CLI integration via Typer test runner
# ---------------------------------------------------------------------------


def test_cli_install_model_command_exists() -> None:
    result = runner.invoke(app, ["install-model", "--help"])
    assert result.exit_code == 0
    assert "install" in result.output.lower() or "model" in result.output.lower()


def test_cli_install_model_success_output(tmp_path: Path) -> None:
    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        with patch("cli.main.Path.home", return_value=tmp_path):
            result = runner.invoke(
                app,
                ["install-model", "--install-dir", str(tmp_path / "models")],
            )

    assert result.exit_code == 0
    assert "install" in result.output.lower() or "model" in result.output.lower()


def test_cli_install_model_shows_install_path(tmp_path: Path) -> None:
    install_dir = tmp_path / "models"

    with patch("cli.commands.install_model.snapshot_download") as mock_dl:
        mock_dl.side_effect = lambda *a, local_dir, **kw: _fake_snapshot(local_dir)
        result = runner.invoke(
            app,
            ["install-model", "--install-dir", str(install_dir)],
        )

    assert result.exit_code == 0
    assert str(install_dir) in result.output or "openrabbit-reviewer" in result.output
