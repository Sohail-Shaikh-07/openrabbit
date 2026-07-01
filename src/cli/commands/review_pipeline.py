"""Local model review pipeline used by ``openrabbit review``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.base import BaseReviewAgent
from agents.coordinator import CoordinatorGraph
from agents.factory import build_review_agents
from agents.models import AgentResult, ReviewState
from configs.settings import Settings
from ranking.ranker import CommentRanker, RankedFinding


@dataclass(frozen=True)
class ReviewPipelineResult:
    """Structured result from the local agent review pipeline."""

    agent_results: list[AgentResult]
    ranked_findings: list[RankedFinding]


async def run_agent_review(
    pr_payload: Any,
    *,
    settings: Settings | None = None,
    agents: list[BaseReviewAgent] | None = None,
    retrieval_result: Any | None = None,
    ranker: CommentRanker | None = None,
) -> ReviewPipelineResult:
    """Run configured review agents for *pr_payload* and rank their findings."""
    if agents is None:
        if settings is None:
            raise ValueError("settings are required when agents are not provided")
        agents = build_review_agents(settings)

    state: ReviewState = {
        "pr_payload": pr_payload,
        "retrieval_result": retrieval_result,
        "agent_results": [],
        "error": None,
    }
    compiled = CoordinatorGraph(agents=agents).compile()
    result = await compiled.ainvoke(state)
    agent_results = list(result.get("agent_results") or [])
    ranked = (ranker or CommentRanker()).rank(agent_results)
    return ReviewPipelineResult(agent_results=agent_results, ranked_findings=ranked)
