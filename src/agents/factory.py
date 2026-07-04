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
    supported here. More local runtimes can implement the same contract later.
    """
    if model.provider == "ollama":
        return OllamaClient(model=model.model_name)
    if model.provider == "openai":
        if not api_key:
            raise MissingModelAPIKeyError(
                f"Model provider 'openai' requires an API key in {model.api_key_env}."
            )
        return OpenAIClient(api_key=api_key, model=model.model_name)
    if model.provider == "openai-compatible":
        if not model.base_url:
            raise MissingModelBaseURLError(
                "Model provider 'openai-compatible' requires model.base_url."
            )
        if not api_key:
            raise MissingModelAPIKeyError(
                f"Model provider 'openai-compatible' requires an API key in {model.api_key_env}."
            )
        return OpenAICompatibleClient(
            api_key=api_key,
            model=model.model_name,
            base_url=model.base_url,
        )

    raise UnsupportedModelProviderError(
        f"Model provider {model.provider!r} is not wired yet. "
        "Configured providers must implement the LLMClient contract. "
        "Use provider='ollama', provider='openai', or provider='openai-compatible'."
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
            if settings.model.provider in {"openai", "openai-compatible"}
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
