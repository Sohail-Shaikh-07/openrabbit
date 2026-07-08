"""Pydantic models for OpenRabbit configuration.

The on-disk format is ``<repo>/.openrabbit/config.yml``. Environment
variables prefixed with ``OPENRABBIT_`` override individual fields using a
``__`` delimiter (e.g. ``OPENRABBIT_POLLING__INTERVAL_SECONDS=30``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PathInstruction(BaseModel):
    """Path-specific review guidance."""

    model_config = ConfigDict(extra="forbid")

    path: str
    instructions: str

    @field_validator("path", "instructions")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("path instructions must be non-empty")
        return stripped


class ReviewSettings(BaseModel):
    """Which review agents are enabled for this repository."""

    model_config = ConfigDict(extra="forbid")

    security: bool = True
    performance: bool = True
    architecture: bool = True
    bug: bool = True
    test_coverage: bool = True
    style: bool = False
    profile: Literal["chill", "assertive"] = "assertive"
    path_include: list[str] = Field(default_factory=list)
    path_exclude: list[str] = Field(default_factory=list)
    path_instructions: list[PathInstruction] = Field(default_factory=list)
    max_files: int = Field(default=80, ge=1, le=500)
    max_changed_lines: int = Field(default=4000, ge=1, le=50000)
    include_generated: bool = False

    @field_validator("path_include", "path_exclude")
    @classmethod
    def _normalise_path_patterns(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class ModelSettings(BaseModel):
    """Which review model is used and how it is served."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "ollama"
    model_name: str = "openrabbit-reviewer-v1"
    base_model: str = "qwen2.5-coder:7b-instruct"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if not provider:
            raise ValueError("provider must be a non-empty provider name")
        return provider

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip().rstrip("/")
        if stripped and not stripped.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return stripped or None

    @field_validator("api_key_env")
    @classmethod
    def _non_empty_api_key_env(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("api_key_env must be a non-empty environment variable name")
        return value

    @model_validator(mode="after")
    def _validate_provider_shape(self) -> ModelSettings:
        if self.provider == "ollama":
            if self.base_url is not None:
                raise ValueError("model.base_url is not supported for provider 'ollama'")
        elif self.provider == "openai":
            if self.base_url is not None:
                raise ValueError("model.base_url is only supported for OpenAI-compatible providers")
        else:
            if not self.base_url:
                raise ValueError(
                    "model.base_url is required for custom OpenAI-compatible providers"
                )
        return self


class PollingSettings(BaseModel):
    """Polling configuration for the GitHub watcher."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(default=60, ge=5, le=3600)
    max_concurrent_reviews: int = Field(default=1, ge=1, le=16)
    review_cooldown_seconds: int = Field(default=0, ge=0, le=86400)
    max_changed_files: int | None = Field(default=None, ge=1, le=1000)


class RepositorySettings(BaseModel):
    """Which repository OpenRabbit watches when no ``--repo`` flag is given."""

    model_config = ConfigDict(extra="forbid")

    target: str | None = None

    @field_validator("target")
    @classmethod
    def _validate_owner_repo(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value.count("/") != 1 or not all(part.strip() for part in value.split("/")):
            raise ValueError("repository.target must be in 'owner/repo' form")
        return value


class GithubSettings(BaseModel):
    """GitHub credentials and behavior knobs.

    ``token`` may be set directly in YAML (not recommended), via the named
    ``token_env`` variable, or via the explicit override
    ``OPENRABBIT_GITHUB__TOKEN``. Resolution order is handled in the loader.
    """

    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    token_env: str = "GITHUB_TOKEN"

    @field_validator("token_env")
    @classmethod
    def _non_empty_token_env(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("token_env must be a non-empty environment variable name")
        return value


class MemorySettings(BaseModel):
    """Local PR memory settings.

    Memory is local-first and stores only derived review metadata. The default
    path is resolved beside the repository config by :class:`Settings`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    path: str | None = None
    learnings_enabled: bool = True

    @field_validator("path")
    @classmethod
    def _normalise_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
