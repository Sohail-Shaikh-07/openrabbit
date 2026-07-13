"""Tests for ``configs`` loader and schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.templates import CONFIG_YML
from configs import (
    ConfigNotFoundError,
    Settings,
    find_config_file,
    find_user_config_file,
    load_settings,
)


def _write_config(tmp_path: Path, body: str, subdir: str = ".openrabbit") -> Path:
    scaffold = tmp_path / subdir
    scaffold.mkdir()
    config = scaffold / "config.yml"
    config.write_text(body, encoding="utf-8")
    return config


def _write_user_config(home: Path, body: str) -> Path:
    scaffold = home / ".openrabbit"
    scaffold.mkdir()
    config = scaffold / "config.yml"
    config.write_text(body, encoding="utf-8")
    return config


def test_defaults_match_init_template(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})

    assert settings.model_dump() == Settings().model_dump()


def test_quality_gate_settings_are_safe_by_default(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})

    assert settings.quality.enabled is False
    assert settings.quality.auto_detect is True
    assert settings.quality.tools == []
    assert settings.quality.timeout_seconds == 120
    assert settings.quality.max_diagnostics == 100


def test_quality_gate_settings_validate_tools(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "quality:\n  enabled: true\n  auto_detect: false\n  tools: [ruff, mypy, pytest]\n",
    )

    settings = load_settings(tmp_path, env={})

    assert settings.quality.tools == ["ruff", "mypy", "pytest"]


def test_quality_gate_settings_reject_unknown_tools(tmp_path: Path) -> None:
    _write_config(tmp_path, "quality:\n  enabled: true\n  tools: [arbitrary-shell-command]\n")

    with pytest.raises(ValueError, match="unsupported quality tool"):
        load_settings(tmp_path, env={})


def test_settings_resolve_repository_workspace(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo / "src", env={})

    assert settings.resolved_workspace_root() == scaffold_repo.resolve()


def test_load_settings_resolves_memory_path_under_openrabbit_state(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})

    assert (
        settings.resolved_memory_path() == scaffold_repo / ".openrabbit" / "state" / "openrabbit.db"
    )


def test_load_settings_walks_up_from_subdir(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_YML)
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)

    settings = load_settings(nested, env={})

    assert settings.polling.interval_seconds == 60


def test_env_overrides_typed_values(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_YML)
    env = {
        "OPENRABBIT_POLLING__INTERVAL_SECONDS": "30",
        "OPENRABBIT_REVIEW__STYLE": "true",
    }

    settings = load_settings(tmp_path, env=env)

    assert settings.polling.interval_seconds == 30
    assert settings.review.style is True


def test_review_controls_load_from_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
review:
  profile: chill
  path_include:
    - "src/**"
  path_exclude:
    - "src/generated/**"
  max_files: 12
  max_changed_lines: 500
  include_generated: false
  path_instructions:
    - path: "src/api/**"
      instructions: "Require explicit authorization checks."
""",
    )

    settings = load_settings(tmp_path, env={})

    assert settings.review.profile == "chill"
    assert settings.review.path_include == ["src/**"]
    assert settings.review.path_exclude == ["src/generated/**"]
    assert settings.review.max_files == 12
    assert settings.review.max_changed_lines == 500
    assert settings.review.include_generated is False
    assert settings.review.path_instructions[0].path == "src/api/**"


def test_review_profile_rejects_unknown_value(tmp_path: Path) -> None:
    _write_config(tmp_path, "review:\n  profile: noisy\n")

    with pytest.raises(ValueError, match="profile"):
        load_settings(tmp_path, env={})


def test_user_config_loads_when_repo_config_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    _write_user_config(
        home,
        "polling:\n  interval_seconds: 45\nrepository:\n  target: owner/repo\n",
    )

    settings = load_settings(workspace, env={}, home=home)

    assert settings.polling.interval_seconds == 45
    assert settings.repository.target == "owner/repo"


def test_repo_config_overrides_user_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    _write_user_config(home, "polling:\n  interval_seconds: 45\nreview:\n  style: true\n")
    _write_config(repo, "polling:\n  interval_seconds: 30\n")

    settings = load_settings(repo, env={}, home=home)

    assert settings.polling.interval_seconds == 30
    assert settings.review.style is True


def test_env_overrides_user_and_repo_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    _write_user_config(home, "polling:\n  interval_seconds: 45\n")
    _write_config(repo, "polling:\n  interval_seconds: 30\n")

    settings = load_settings(
        repo,
        env={"OPENRABBIT_POLLING__INTERVAL_SECONDS": "15"},
        home=home,
    )

    assert settings.polling.interval_seconds == 15


def test_repo_search_does_not_double_load_user_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    nested = home / "projects" / "repo"
    home.mkdir()
    nested.mkdir(parents=True)
    _write_user_config(home, "polling:\n  interval_seconds: 45\n")

    settings = load_settings(nested, env={}, home=home)

    assert settings.polling.interval_seconds == 45


def test_find_user_config_file_returns_optional_home_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = _write_user_config(home, "polling:\n  interval_seconds: 45\n")

    assert find_user_config_file(home) == config


def test_find_user_config_file_returns_none_when_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    assert find_user_config_file(home) is None


def test_user_config_rejects_inline_model_secrets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    _write_user_config(home, "model:\n  provider: openai\n  api_key: sk-secret-value\n")

    with pytest.raises(ValueError) as exc:
        load_settings(workspace, env={}, home=home)

    message = str(exc.value)
    assert "model.api_key" in message
    assert "sk-secret-value" not in message


def test_github_token_resolution_prefers_explicit_override(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_YML)
    env = {
        "OPENRABBIT_GITHUB__TOKEN": "explicit-token",
        "GITHUB_TOKEN": "ambient-token",
    }

    settings = load_settings(tmp_path, env=env)

    assert settings.resolved_github_token(env=env) == "explicit-token"


def test_github_token_resolution_falls_back_to_named_env(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_YML)
    env = {"GITHUB_TOKEN": "ambient-token"}

    settings = load_settings(tmp_path, env=env)

    assert settings.resolved_github_token(env=env) == "ambient-token"


def test_github_token_resolution_uses_windows_user_env_when_process_env_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_config(tmp_path, CONFIG_YML)

    monkeypatch.setattr("configs.settings._persistent_windows_env", lambda name: "user-token")
    settings = load_settings(tmp_path, env={})

    assert settings.resolved_github_token(env={}) == "user-token"


def test_github_token_resolution_keeps_process_env_before_windows_user_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_config(tmp_path, CONFIG_YML)
    env = {"GITHUB_TOKEN": "process-token"}

    monkeypatch.setattr("configs.settings._persistent_windows_env", lambda name: "user-token")
    settings = load_settings(tmp_path, env=env)

    assert settings.resolved_github_token(env=env) == "process-token"


def test_github_token_resolution_returns_none_when_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_config(tmp_path, CONFIG_YML)
    monkeypatch.setattr("configs.settings._persistent_windows_env", lambda name: None)

    settings = load_settings(tmp_path, env={})

    assert settings.resolved_github_token(env={}) is None


def test_model_api_key_resolution_uses_configured_env(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "model:\n  provider: openai\n  model_name: gpt-4.1-mini\n  api_key_env: MY_OPENAI_KEY\n",
    )
    env = {"MY_OPENAI_KEY": "sk-test"}

    settings = load_settings(tmp_path, env=env)

    assert settings.model.provider == "openai"
    assert settings.resolved_model_api_key(env=env) == "sk-test"


def test_openai_compatible_provider_supports_base_url(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "\n".join(
            [
                "model:",
                "  provider: openai-compatible",
                "  model_name: openai/gpt-oss-20b",
                "  base_url: 'http://localhost:8000/v1/'",
                "  api_key_env: OPENAI_COMPATIBLE_API_KEY",
            ]
        ),
    )

    settings = load_settings(tmp_path, env={})

    assert settings.model.provider == "openai-compatible"
    assert settings.model.model_name == "openai/gpt-oss-20b"
    assert settings.model.base_url == "http://localhost:8000/v1"
    assert settings.model.api_key_env == "OPENAI_COMPATIBLE_API_KEY"


def test_custom_openai_compatible_provider_name_supports_base_url(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "\n".join(
            [
                "model:",
                "  provider: openrouter",
                "  model_name: openai/gpt-oss-20b",
                "  base_url: 'https://openrouter.ai/api/v1/'",
                "  api_key_env: OPENROUTER_API_KEY",
            ]
        ),
    )

    settings = load_settings(tmp_path, env={})

    assert settings.model.provider == "openrouter"
    assert settings.model.model_name == "openai/gpt-oss-20b"
    assert settings.model.base_url == "https://openrouter.ai/api/v1"
    assert settings.model.api_key_env == "OPENROUTER_API_KEY"


def test_openai_compatible_provider_requires_base_url(tmp_path: Path) -> None:
    _write_config(tmp_path, "model:\n  provider: openai-compatible\n")

    with pytest.raises(ValueError, match=r"model\.base_url"):
        load_settings(tmp_path, env={})


def test_custom_provider_requires_base_url(tmp_path: Path) -> None:
    _write_config(tmp_path, "model:\n  provider: openrouter\n")

    with pytest.raises(ValueError, match=r"model\.base_url"):
        load_settings(tmp_path, env={})


def test_model_base_url_requires_http_scheme(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "model:\n  provider: openai-compatible\n  base_url: localhost:8000/v1\n",
    )

    with pytest.raises(ValueError, match="base_url"):
        load_settings(tmp_path, env={})


def test_model_base_url_rejected_for_official_openai(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "model:\n  provider: openai\n  base_url: https://gateway.example.com/v1\n",
    )

    with pytest.raises(ValueError, match="only supported"):
        load_settings(tmp_path, env={})


def test_model_base_url_rejected_for_ollama(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "model:\n  provider: ollama\n  base_url: http://localhost:11434\n",
    )

    with pytest.raises(ValueError, match="not supported"):
        load_settings(tmp_path, env={})


def test_inline_model_api_key_is_rejected_without_leaking_value(tmp_path: Path) -> None:
    _write_config(tmp_path, "model:\n  provider: openai\n  api_key: sk-secret-value\n")

    with pytest.raises(ValueError) as exc:
        load_settings(tmp_path, env={})

    message = str(exc.value)
    assert "model.api_key" in message
    assert "api_key_env" in message
    assert "sk-secret-value" not in message


def test_inline_model_secret_env_override_is_rejected_without_leaking_value(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, CONFIG_YML)

    with pytest.raises(ValueError) as exc:
        load_settings(tmp_path, env={"OPENRABBIT_MODEL__API_KEY": "sk-env-secret"})

    message = str(exc.value)
    assert "model.api_key" in message
    assert "api_key_env" in message
    assert "sk-env-secret" not in message


def test_model_api_key_resolution_uses_windows_user_env_when_process_env_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_config(tmp_path, "model:\n  provider: openai\n")

    monkeypatch.setattr("configs.settings._persistent_windows_env", lambda name: "sk-user")
    settings = load_settings(tmp_path, env={})

    assert settings.resolved_model_api_key(env={}) == "sk-user"


def test_model_api_key_env_must_be_non_empty(tmp_path: Path) -> None:
    _write_config(tmp_path, "model:\n  api_key_env: ' '\n")

    with pytest.raises(ValueError):
        load_settings(tmp_path, env={})


def test_missing_scaffold_raises_helpful_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigNotFoundError) as exc:
        load_settings(tmp_path, env={})

    assert "openrabbit init" in str(exc.value)


def test_extra_top_level_keys_are_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path, "unknown: 1\n")

    with pytest.raises(ValueError) as exc:
        load_settings(tmp_path, env={})

    assert "unknown" in str(exc.value)


def test_invalid_provider_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path, "model:\n  provider: ftp\n")

    with pytest.raises(ValueError, match=r"model\.base_url"):
        load_settings(tmp_path, env={})


def test_find_config_file_returns_first_hit(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_YML)

    found = find_config_file(tmp_path)

    assert found == tmp_path / ".openrabbit" / "config.yml"


def test_legacy_codereviewer_config_still_loads(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_YML, subdir=".codereviewer")

    found = find_config_file(tmp_path)

    assert found == tmp_path / ".codereviewer" / "config.yml"


def test_openrabbit_config_wins_over_legacy_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "polling:\n  interval_seconds: 120\n", subdir=".codereviewer")
    _write_config(tmp_path, "polling:\n  interval_seconds: 30\n", subdir=".openrabbit")

    settings = load_settings(tmp_path, env={})

    assert settings.polling.interval_seconds == 30


def test_polling_interval_lower_bound_enforced(tmp_path: Path) -> None:
    _write_config(tmp_path, "polling:\n  interval_seconds: 1\n")

    with pytest.raises(ValueError):
        load_settings(tmp_path, env={})


def test_polling_automation_controls_load(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "\n".join(
            [
                "polling:",
                "  interval_seconds: 30",
                "  max_concurrent_reviews: 3",
                "  review_cooldown_seconds: 120",
                "  max_changed_files: 50",
            ]
        ),
    )

    settings = load_settings(tmp_path, env={})

    assert settings.polling.max_concurrent_reviews == 3
    assert settings.polling.review_cooldown_seconds == 120
    assert settings.polling.max_changed_files == 50


def test_polling_automation_control_bounds_are_enforced(tmp_path: Path) -> None:
    _write_config(tmp_path, "polling:\n  max_concurrent_reviews: 0\n")

    with pytest.raises(ValueError):
        load_settings(tmp_path, env={})


def test_non_mapping_yaml_is_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(ValueError):
        load_settings(tmp_path, env={})
