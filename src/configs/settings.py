"""Root ``Settings`` class and YAML loader.

Two entry points:

- :func:`load_settings` reads layered OpenRabbit config and merges
  environment variables.
- :class:`Settings` is the Pydantic root model; instantiate it directly with
  a dict (mostly for testing).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, PrivateAttr

from configs.schema import (
    GithubSettings,
    MemorySettings,
    ModelSettings,
    PollingSettings,
    RepositorySettings,
    ReviewSettings,
)

CONFIG_SUBDIR = ".openrabbit"
LEGACY_CONFIG_SUBDIR = ".codereviewer"
CONFIG_FILENAME = "config.yml"
USER_CONFIG_DIR = ".openrabbit"
ENV_PREFIX = "OPENRABBIT_"
ENV_DELIMITER = "__"
_MODEL_SECRET_KEY_MARKERS = ("api_key", "secret", "token", "password", "credential")
_MODEL_SECRET_SAFE_KEYS = {"api_key_env"}


class ConfigNotFoundError(FileNotFoundError):
    """Raised when no OpenRabbit config file can be located."""


class Settings(BaseModel):
    """Root OpenRabbit configuration."""

    model_config = ConfigDict(extra="forbid")

    review: ReviewSettings = ReviewSettings()
    model: ModelSettings = ModelSettings()
    polling: PollingSettings = PollingSettings()
    github: GithubSettings = GithubSettings()
    repository: RepositorySettings = RepositorySettings()
    memory: MemorySettings = MemorySettings()
    _config_dir: Path | None = PrivateAttr(default=None)

    def resolved_github_token(self, env: dict[str, str] | None = None) -> str | None:
        """Return the GitHub token using the documented precedence rules.

        Order:

        1. ``OPENRABBIT_GITHUB__TOKEN`` env override (already merged into
           ``self.github.token`` by :func:`load_settings`).
        2. The env variable named by ``github.token_env`` (default ``GITHUB_TOKEN``).
        """
        if self.github.token:
            return self.github.token
        source = env if env is not None else os.environ
        token = source.get(self.github.token_env)
        if token:
            return token
        return _persistent_windows_env(self.github.token_env)

    def resolved_model_api_key(self, env: dict[str, str] | None = None) -> str | None:
        """Return the model provider API key from the configured environment name."""
        source = env if env is not None else os.environ
        token = source.get(self.model.api_key_env)
        if token:
            return token
        return _persistent_windows_env(self.model.api_key_env)

    def resolved_memory_path(self) -> Path:
        """Return the SQLite path used for local PR memory."""
        if self.memory.path:
            raw = Path(self.memory.path).expanduser()
            if raw.is_absolute():
                return raw
            base = self._config_dir or Path.cwd() / CONFIG_SUBDIR
            return (base / raw).resolve()
        base = self._config_dir or Path.cwd() / CONFIG_SUBDIR
        return base / "state" / "openrabbit.db"


def find_config_file(start: Path) -> Path:
    """Locate the config file by walking up from ``start``.

    Raises:
        ConfigNotFoundError: If no ``.openrabbit/config.yml`` or legacy
            ``.codereviewer/config.yml`` is found in
            ``start`` or any of its parents.
    """
    start = start.resolve()
    for directory in (start, *start.parents):
        candidates = (
            directory / CONFIG_SUBDIR / CONFIG_FILENAME,
            directory / LEGACY_CONFIG_SUBDIR / CONFIG_FILENAME,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    raise ConfigNotFoundError(
        f"No {CONFIG_SUBDIR}/{CONFIG_FILENAME} or "
        f"{LEGACY_CONFIG_SUBDIR}/{CONFIG_FILENAME} found in {start} or any parent. "
        f"Run `openrabbit init` first, or create ~/{USER_CONFIG_DIR}/{CONFIG_FILENAME} "
        "for user-level defaults."
    )


def find_user_config_file(home: Path | None = None) -> Path | None:
    """Return the optional user-level config file."""
    root = (home or Path.home()).resolve()
    candidate = root / USER_CONFIG_DIR / CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def load_settings(
    start: Path | None = None,
    *,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> Settings:
    """Load and validate layered settings from disk and the environment.

    Precedence, from weakest to strongest:

    1. Schema defaults.
    2. Optional user config at ``~/.openrabbit/config.yml``.
    3. Repository config at ``.openrabbit/config.yml`` or legacy
       ``.codereviewer/config.yml`` found by walking up from ``start``.
    4. ``OPENRABBIT_...`` environment overrides.

    Args:
        start: Directory to begin the upward search from. Defaults to the
            current working directory.
        env: Environment mapping. Defaults to :data:`os.environ`. Passed in
            during tests to avoid global state.
        home: Home directory used to locate the optional user config. Defaults
            to :func:`Path.home`. Passed in during tests to avoid global state.

    Returns:
        A validated :class:`Settings` instance.
    """
    env_map = env if env is not None else dict(os.environ)
    start = start or Path.cwd()

    user_config_path = find_user_config_file(home)
    user_raw = _read_optional_config(user_config_path)

    repo_config_path = _find_repo_config_file(start, user_config_path=user_config_path)
    if repo_config_path is None and user_config_path is None:
        raise ConfigNotFoundError(
            f"No {CONFIG_SUBDIR}/{CONFIG_FILENAME}, "
            f"{LEGACY_CONFIG_SUBDIR}/{CONFIG_FILENAME}, or "
            f"~/{USER_CONFIG_DIR}/{CONFIG_FILENAME} found. "
            "Run `openrabbit init` first."
        )
    repo_raw = _read_optional_config(repo_config_path)

    raw = _deep_merge(user_raw, repo_raw)
    overrides = _env_overrides(env_map)
    _reject_inline_model_secrets(overrides)
    merged = _deep_merge(raw, overrides)
    settings = Settings.model_validate(merged)
    if repo_config_path is not None:
        settings._config_dir = repo_config_path.parent
    elif user_config_path is not None:
        settings._config_dir = user_config_path.parent
    return settings


def _find_repo_config_file(start: Path, *, user_config_path: Path | None) -> Path | None:
    try:
        config_path = find_config_file(start)
    except ConfigNotFoundError:
        return None

    if user_config_path is not None and _same_path(config_path, user_config_path):
        return None
    return config_path


def _read_optional_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    raw = _read_yaml(path)
    _reject_inline_model_secrets(raw)
    return raw


def _read_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return {key: ({} if value is None else value) for key, value in loaded.items()}


def _env_overrides(env: dict[str, str]) -> dict[str, Any]:
    """Translate ``OPENRABBIT_SECTION__FIELD=value`` env vars into nested dicts."""
    out: dict[str, Any] = {}
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX) :].lower().split(ENV_DELIMITER)
        if not all(part for part in path):
            continue
        cursor = out
        for segment in path[:-1]:
            cursor = cursor.setdefault(segment, {})
            if not isinstance(cursor, dict):
                # A leaf value was set earlier by a more specific override.
                # Skip this conflicting entry rather than corrupting the tree.
                cursor = {}
                break
        cursor[path[-1]] = _coerce_scalar(value)
    return out


def _coerce_scalar(value: str) -> bool | int | float | str:
    lowered = value.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _reject_inline_model_secrets(config: dict[str, Any]) -> None:
    model_config = config.get("model")
    if not isinstance(model_config, dict):
        return

    for key in model_config:
        normalized = str(key).strip().lower()
        if normalized in _MODEL_SECRET_SAFE_KEYS:
            continue
        if any(marker in normalized for marker in _MODEL_SECRET_KEY_MARKERS):
            raise ValueError(
                f"model.{key} is not supported. Store provider secrets in an "
                "environment variable and set model.api_key_env to that variable name."
            )


def _persistent_windows_env(name: str) -> str | None:
    """Read a persistent Windows User/Machine env var when process env is stale."""
    if sys.platform != "win32":
        return None

    try:
        import winreg
    except ImportError:  # pragma: no cover - defensive for unusual runtimes
        return None

    for root, path in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ):
        try:
            with winreg.OpenKey(root, path) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        text = str(value).strip()
        if text:
            return text

    return None
