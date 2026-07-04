"""Multi-agent review system (Phase 4).

Agents are organized as small, single-responsibility modules orchestrated via
LangGraph. The coordinator fans out work to specialized review agents in
parallel and merges their findings before ranking.
"""

from __future__ import annotations

from agents.architecture import ArchitectureAgent
from agents.base import BaseReviewAgent
from agents.bugs import BugDetectionAgent
from agents.coordinator import CoordinatorGraph
from agents.factory import (
    MissingModelAPIKeyError,
    MissingModelBaseURLError,
    UnsupportedModelProviderError,
    build_llm_client,
    build_review_agents,
)
from agents.llm import LLMClient, OllamaClient, OpenAIClient, OpenAICompatibleClient
from agents.models import AgentResult, Finding, ReviewState, Severity
from agents.performance import PerformanceAgent
from agents.security import SecurityAgent
from agents.test_coverage import TestCoverageAgent

__all__ = [
    "AgentResult",
    "ArchitectureAgent",
    "BaseReviewAgent",
    "BugDetectionAgent",
    "CoordinatorGraph",
    "Finding",
    "LLMClient",
    "MissingModelAPIKeyError",
    "MissingModelBaseURLError",
    "OllamaClient",
    "OpenAIClient",
    "OpenAICompatibleClient",
    "PerformanceAgent",
    "ReviewState",
    "SecurityAgent",
    "Severity",
    "TestCoverageAgent",
    "UnsupportedModelProviderError",
    "build_llm_client",
    "build_review_agents",
]
