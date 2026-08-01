"""Tests for the optional ``openrabbit server`` command."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from api import (
    GITHUB_WEBHOOK_PATH,
    HEALTH_PATH,
    DeliveryStateError,
    SQLiteDeliveryStateStore,
    delivery_state_path,
)
from cli.commands.server import ServerError, run_server
from cli.main import app
from configs import RepositorySettings, Settings, WebhookSettings

_RUNNER = CliRunner()


def _settings(*, enabled: bool = True) -> Settings:
    return Settings(
        webhook=WebhookSettings(
            enabled=enabled,
            secret_env="TEST_WEBHOOK_SECRET",
        ),
        repository=RepositorySettings(target="o/r"),
    )


def test_run_server_launches_uvicorn_with_resolved_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TEST_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    settings = _settings()
    settings._workspace_root = tmp_path

    with patch("cli.commands.server.uvicorn.run") as uvicorn_run:
        run_server(settings, host=" 127.0.0.1 ", port=8010)

    uvicorn_run.assert_called_once()
    server_app = uvicorn_run.call_args.args[0]
    assert uvicorn_run.call_args.kwargs == {"host": "127.0.0.1", "port": 8010}
    client = TestClient(server_app)
    assert client.get(HEALTH_PATH).status_code == 200
    payload = b'{"repository":{"full_name":"o/r"}}'
    signature = hmac.new(b"test-secret", payload, hashlib.sha256).hexdigest()
    response = client.post(
        GITHUB_WEBHOOK_PATH,
        content=payload,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "ping-delivery",
            "X-Hub-Signature-256": f"sha256={signature}",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    assert response.json()["reason"] == "ping"
    assert response.json()["dispatched"] is False
    duplicate = client.post(
        GITHUB_WEBHOOK_PATH,
        content=payload,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "ping-delivery",
            "X-Hub-Signature-256": f"sha256={signature}",
            "Content-Type": "application/json",
        },
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["reason"] == "duplicate_ignored"
    record = SQLiteDeliveryStateStore(delivery_state_path(tmp_path)).get("ping-delivery")
    assert record is not None
    assert record.status == "ignored"


def test_run_server_rejects_disabled_webhook_mode() -> None:
    with pytest.raises(ServerError, match="webhook mode is disabled"):
        run_server(_settings(enabled=False), host="127.0.0.1", port=8000)


def test_run_server_rejects_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr("configs.settings._persistent_windows_env", lambda name: None)

    with pytest.raises(ServerError, match="TEST_WEBHOOK_SECRET"):
        run_server(_settings(), host="127.0.0.1", port=8000)


def test_run_server_requires_target_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_WEBHOOK_SECRET", "test-secret")
    settings = Settings(
        webhook=WebhookSettings(
            enabled=True,
            secret_env="TEST_WEBHOOK_SECRET",
        )
    )

    with pytest.raises(ServerError, match=r"repository\.target"):
        run_server(settings, host="127.0.0.1", port=8000)


def test_run_server_reports_delivery_state_startup_failure_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    client = AsyncMock()

    with (
        patch("cli.commands.server.GitHubClient.from_settings", return_value=client),
        patch(
            "cli.commands.server.SQLiteDeliveryStateStore",
            side_effect=DeliveryStateError("private path"),
        ),
        pytest.raises(ServerError, match="delivery state could not be initialized"),
    ):
        run_server(_settings(), host="127.0.0.1", port=8000)

    client.aclose.assert_awaited_once()


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
