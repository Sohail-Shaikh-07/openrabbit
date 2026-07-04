"""Factory helpers for configured review agents."""

from __future__ import annotations

from agents.architecture import ArchitectureAgent
from agents.base import BaseReviewAgent
from agents.bugs import BugDetectionAgent
from agents.llm import LLMClient, OllamaClient, OpenAIClient
from agents.performance import PerformanceAgent
from agents.security import SecurityAgent
from agents.test_coverage import TestCoverageAgent
from configs.schema import ModelSettings
from configs.settings import Settings


class UnsupportedModelProviderError(NotImplementedError):
    """Raised when a configured model provider has no client implementation."""


class MissingModelAPIKeyError(ValueError):
    """Raised when a hosted model provider needs an API key that is not set."""


def build_llm_client(model: ModelSettings, *, api_key: str | None = None) -> LLMClient:
    """Return a model client for the configured provider.

    The official OpenAI provider is supported here. OpenAI-compatible custom
    base URLs are intentionally left for the follow-up provider task.
    """
    if model.provider == "ollama":
        return OllamaClient(model=model.model_name)
    if model.provider == "openai":
        if not api_key:
            raise MissingModelAPIKeyError(
                f"Model provider 'openai' requires an API key in {model.api_key_env}."
            )
        return OpenAIClient(api_key=api_key, model=model.model_name)

    raise UnsupportedModelProviderError(
        f"Model provider {model.provider!r} is not wired yet. "
        "Configured providers must implement the LLMClient contract. "
        "Use provider='ollama' or provider='openai'."
    )


def build_review_agents(
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
) -> list[BaseReviewAgent]:
    """Return enabled review agents using the configured model runtime."""
    client = build_llm_client(
        settings.model,
        api_key=(
            settings.resolved_model_api_key(env=env)
            if settings.model.provider == "openai"
            else None
        ),
    )
    agents: list[BaseReviewAgent] = []

    if settings.review.security:
        agents.append(SecurityAgent(client=client))
    if settings.review.performance:
        agents.append(PerformanceAgent(client=client))
    if settings.review.architecture:
        agents.append(ArchitectureAgent(client=client))
    if settings.review.bug:
        agents.append(BugDetectionAgent(client=client))
    if settings.review.test_coverage:
        agents.append(TestCoverageAgent(client=client))

    return agents
