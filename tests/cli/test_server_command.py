"""Tests for the optional ``openrabbit server`` command."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from api import HEALTH_PATH
from cli.commands.server import ServerError, run_server
from cli.main import app
from configs import Settings, WebhookSettings

_RUNNER = CliRunner()


def _settings(*, enabled: bool = True) -> Settings:
    return Settings(
        webhook=WebhookSettings(
            enabled=enabled,
            secret_env="TEST_WEBHOOK_SECRET",
        )
    )


def test_run_server_launches_uvicorn_with_resolved_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_WEBHOOK_SECRET", "test-secret")

    with patch("cli.commands.server.uvicorn.run") as uvicorn_run:
        run_server(_settings(), host=" 127.0.0.1 ", port=8010)

    uvicorn_run.assert_called_once()
    server_app = uvicorn_run.call_args.args[0]
    assert uvicorn_run.call_args.kwargs == {"host": "127.0.0.1", "port": 8010}
    assert TestClient(server_app).get(HEALTH_PATH).status_code == 200


def test_run_server_rejects_disabled_webhook_mode() -> None:
    with pytest.raises(ServerError, match="webhook mode is disabled"):
        run_server(_settings(enabled=False), host="127.0.0.1", port=8000)


def test_run_server_rejects_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr("configs.settings._persistent_windows_env", lambda name: None)

    with pytest.raises(ServerError, match="TEST_WEBHOOK_SECRET"):
        run_server(_settings(), host="127.0.0.1", port=8000)


@pytest.mark.parametrize(
    "host,port,message",
    [
        (" ", 8000, "host must not be empty"),
        ("127.0.0.1", 0, "port must be between"),
        ("127.0.0.1", 65_536, "port must be between"),
    ],
)
def test_run_server_rejects_invalid_network_options(
    host: str,
    port: int,
    message: str,
) -> None:
    with pytest.raises(ServerError, match=message):
        run_server(_settings(), host=host, port=port)


def test_server_cli_uses_localhost_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    monkeypatch.setattr("cli.main._load_settings_or_exit", lambda workspace: settings)

    with patch("cli.main.run_server") as server_run:
        result = _RUNNER.invoke(app, ["server"])

    assert result.exit_code == 0
    server_run.assert_called_once_with(settings, host="127.0.0.1", port=8000)


def test_server_cli_reports_safe_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    monkeypatch.setattr("cli.main._load_settings_or_exit", lambda workspace: settings)

    with patch("cli.main.run_server", side_effect=ServerError("webhook mode is disabled")):
        result = _RUNNER.invoke(app, ["server"])

    assert result.exit_code == 1
    assert "webhook mode is disabled" in result.output
