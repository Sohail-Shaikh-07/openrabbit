"""Factory helpers for configured review agents."""

from __future__ import annotations

from agents.architecture import ArchitectureAgent
from agents.base import BaseReviewAgent
from agents.bugs import BugDetectionAgent
from agents.llm import LLMClient, OllamaClient, OpenAIClient, OpenAICompatibleClient
from agents.performance import PerformanceAgent
from agents.security import SecurityAgent
from agents.test_coverage import TestCoverageAgent
from configs.schema import ModelSettings
from configs.settings import Settings


class UnsupportedModelProviderError(NotImplementedError):
    """Raised when a configured model provider has no client implementation."""


class MissingModelAPIKeyError(ValueError):
    """Raised when a hosted model provider needs an API key that is not set."""


class MissingModelBaseURLError(ValueError):
    """Raised when an OpenAI-compatible provider has no base URL configured."""


def build_llm_client(model: ModelSettings, *, api_key: str | None = None) -> LLMClient:
    """Return a model client for the configured provider.

    Official OpenAI and OpenAI-compatible chat/completions providers are
    supported here. Any non-``ollama``/``openai`` provider name with a
    ``base_url`` is treated as an OpenAI-compatible endpoint and preserved as
    the diagnostic provider name.
    """
    if model.provider == "ollama":
        return OllamaClient(model=model.model_name)
    if model.provider == "openai":
        if not api_key:
            raise MissingModelAPIKeyError(
                f"Model provider 'openai' requires an API key in {model.api_key_env}."
            )
        return OpenAIClient(api_key=api_key, model=model.model_name)
    if model.provider != "ollama":
        if not model.base_url:
            raise MissingModelBaseURLError(
                f"Model provider {model.provider!r} requires model.base_url."
            )
        if not api_key:
            raise MissingModelAPIKeyError(
                f"Model provider {model.provider!r} requires an API key in {model.api_key_env}."
            )
        return OpenAICompatibleClient(
            api_key=api_key,
            model=model.model_name,
            base_url=model.base_url,
            provider_name=model.provider,
        )

    raise UnsupportedModelProviderError(
        f"Model provider {model.provider!r} is not wired yet. "
        "Configured providers must implement the LLMClient contract. "
        "Use provider='ollama', provider='openai', or a custom provider with model.base_url."
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
            if settings.model.provider != "ollama"
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
