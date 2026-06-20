"""Multi-agent review system (Phase 4).

Agents are organized as small, single-responsibility modules orchestrated via
LangGraph. The coordinator fans out work to specialized review agents in
parallel and merges their findings before ranking.
"""

from __future__ import annotations

from agents.base import BaseReviewAgent
from agents.coordinator import CoordinatorGraph
from agents.models import AgentResult, Finding, ReviewState, Severity

__all__ = [
    "AgentResult",
    "BaseReviewAgent",
    "CoordinatorGraph",
    "Finding",
    "ReviewState",
    "Severity",
]
