"""Factory helpers for configured review agents."""

from __future__ import annotations

from agents.architecture import ArchitectureAgent
from agents.base import BaseReviewAgent
from agents.bugs import BugDetectionAgent
from agents.llm import LLMClient, OllamaClient
from agents.performance import PerformanceAgent
from agents.security import SecurityAgent
from agents.test_coverage import TestCoverageAgent
from configs.schema import ModelSettings
from configs.settings import Settings


class UnsupportedModelProviderError(NotImplementedError):
    """Raised when a configured model provider has no client implementation."""


def build_llm_client(model: ModelSettings) -> LLMClient:
    """Return a model client for the configured provider.

    OP-49 deliberately wires only the existing Ollama runtime. Hosted OpenAI
    and OpenAI-compatible clients will reuse this entry point in the follow-up tasks.
    """
    if model.provider == "ollama":
        return OllamaClient(model=model.model_name)

    raise UnsupportedModelProviderError(
        f"Model provider {model.provider!r} is not wired yet. "
        "Configured providers must implement the LLMClient contract. "
        "Use provider='ollama' until the API provider tasks land."
    )


def build_review_agents(settings: Settings) -> list[BaseReviewAgent]:
    """Return enabled review agents using the configured model runtime."""
    client = build_llm_client(settings.model)
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
