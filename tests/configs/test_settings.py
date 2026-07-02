"""Tests for ``configs`` loader and schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.templates import CONFIG_YML
from configs import (
    ConfigNotFoundError,
    Settings,
    find_config_file,
    load_settings,
)


def _write_config(tmp_path: Path, body: str, subdir: str = ".openrabbit") -> Path:
    scaffold = tmp_path / subdir
    scaffold.mkdir()
    config = scaffold / "config.yml"
    config.write_text(body, encoding="utf-8")
    return config


def test_defaults_match_init_template(scaffold_repo: Path) -> None:
    settings = load_settings(scaffold_repo, env={})

    assert settings == Settings()


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

    with pytest.raises(ValueError):
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


def test_non_mapping_yaml_is_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(ValueError):
        load_settings(tmp_path, env={})
