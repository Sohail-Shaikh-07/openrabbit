"""Pydantic models for OpenRabbit configuration.

The on-disk format is ``<repo>/.codereviewer/config.yml``. Environment
variables prefixed with ``OPENRABBIT_`` override individual fields using a
``__`` delimiter (e.g. ``OPENRABBIT_POLLING__INTERVAL_SECONDS=30``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ModelProvider = Literal["ollama", "vllm", "transformers"]


class ReviewSettings(BaseModel):
    """Which review agents are enabled for this repository."""

    security: bool = True
    performance: bool = True
    architecture: bool = True
    bug: bool = True
    test_coverage: bool = True
    style: bool = False


class ModelSettings(BaseModel):
    """Which review model is used and how it is served."""

    provider: ModelProvider = "ollama"
    model_name: str = "openrabbit-reviewer-v1"
    base_model: str = "qwen2.5-coder:7b-instruct"


class PollingSettings(BaseModel):
    """Polling configuration for the GitHub watcher."""

    interval_seconds: int = Field(default=60, ge=5, le=3600)


class RepositorySettings(BaseModel):
    """Which repository OpenRabbit watches when no ``--repo`` flag is given."""

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

    token: str | None = None
    token_env: str = "GITHUB_TOKEN"

    @field_validator("token_env")
    @classmethod
    def _non_empty_token_env(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("token_env must be a non-empty environment variable name")
        return value
