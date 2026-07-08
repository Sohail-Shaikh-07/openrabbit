"""Tests for ``openrabbit model-health``."""

from __future__ import annotations

from typer.testing import CliRunner

from cli.commands.model_health import ModelHealthResult, run_model_health_check_blocking
from cli.main import app
from configs import ModelSettings, Settings

_RUNNER = CliRunner()


class _FakeClient:
    provider_name = "fake-provider"
    model_name = "fake-model"

    def __init__(self, response: str) -> None:
        self._response = response

    async def generate(self, prompt: str) -> str:
        assert "JSON object" in prompt
        return self._response


def test_model_health_check_reports_success() -> None:
    settings = Settings(model=ModelSettings(provider="ollama", model_name="fake-model"))

    result = run_model_health_check_blocking(
        settings,
        client_factory=lambda _model, *, api_key=None: _FakeClient('{"ok": true}'),
    )

    assert result.ok is True
    assert result.provider == "fake-provider"
    assert result.model == "fake-model"
    assert result.message == "Model provider reachable."


def test_model_health_check_reports_empty_response() -> None:
    settings = Settings(model=ModelSettings(provider="ollama", model_name="fake-model"))

    result = run_model_health_check_blocking(
        settings,
        client_factory=lambda _model, *, api_key=None: _FakeClient(""),
    )

    assert result.ok is False
    assert "empty response" in result.message


def test_model_health_check_reports_missing_api_key() -> None:
    missing_key = "OPENRABBIT_TEST_MISSING_MODEL_KEY"
    settings = Settings(
        model=ModelSettings(
            provider="openai",
            model_name="gpt-4.1-mini",
            api_key_env=missing_key,
        )
    )

    result = run_model_health_check_blocking(settings, env={})

    assert result.ok is False
    assert result.provider == "openai"
    assert missing_key in result.message


def test_model_health_cli_prints_success(
    scaffold_repo, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr(
        "cli.main.run_model_health_check_blocking",
        lambda _settings: ModelHealthResult(
            ok=True,
            provider="ollama",
            model="qwen2.5-coder:7b",
            message="Model provider reachable.",
        ),
    )

    result = _RUNNER.invoke(app, ["model-health", "--workspace", str(scaffold_repo)])

    assert result.exit_code == 0
    assert "ollama / qwen2.5-coder:7b" in result.output


def test_model_health_cli_exits_user_error_on_failure(
    scaffold_repo, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr(
        "cli.main.run_model_health_check_blocking",
        lambda _settings: ModelHealthResult(
            ok=False,
            provider="openai",
            model="gpt-4.1-mini",
            message="Model provider health check failed: missing key",
        ),
    )

    result = _RUNNER.invoke(app, ["model-health", "--workspace", str(scaffold_repo)])

    assert result.exit_code == 1
    assert "missing key" in result.output
