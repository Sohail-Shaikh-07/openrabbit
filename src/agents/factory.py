"""Factory helpers for configured review agents."""

from __future__ import annotations

from agents.architecture import ArchitectureAgent
from agents.base import BaseReviewAgent
from agents.bugs import BugDetectionAgent
from agents.llm import OllamaClient
from agents.performance import PerformanceAgent
from agents.security import SecurityAgent
from agents.test_coverage import TestCoverageAgent
from configs.settings import Settings


def build_review_agents(settings: Settings) -> list[BaseReviewAgent]:
    """Return enabled review agents using the configured local model runtime."""
    if settings.model.provider != "ollama":
        raise NotImplementedError(
            f"Model provider {settings.model.provider!r} is not wired yet. "
            "Use provider='ollama' for local reviews."
        )

    client = OllamaClient(model=settings.model.model_name)
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
