"""Local model review pipeline used by ``openrabbit review``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.base import BaseReviewAgent
from agents.coordinator import CoordinatorGraph
from agents.factory import build_review_agents
from agents.models import AgentResult, ReviewState
from configs.settings import Settings
from ranking.grounding import filter_grounded_findings
from ranking.ranker import CommentRanker, RankedFinding


@dataclass(frozen=True)
class ReviewPipelineResult:
    """Structured result from the local agent review pipeline."""

    agent_results: list[AgentResult]
    ranked_findings: list[RankedFinding]
    dropped_findings_count: int = 0


async def run_agent_review(
    pr_payload: Any,
    *,
    settings: Settings | None = None,
    agents: list[BaseReviewAgent] | None = None,
    retrieval_result: Any | None = None,
    pr_history: Any | None = None,
    env: dict[str, str] | None = None,
    ranker: CommentRanker | None = None,
) -> ReviewPipelineResult:
    """Run configured review agents for *pr_payload* and rank their findings."""
    if agents is None:
        if settings is None:
            raise ValueError("settings are required when agents are not provided")
        agents = build_review_agents(settings, env=env)

    state: ReviewState = {
        "pr_payload": pr_payload,
        "retrieval_result": retrieval_result,
        "pr_history": pr_history,
        "agent_results": [],
        "error": None,
    }
    compiled = CoordinatorGraph(agents=agents).compile()
    result = await compiled.ainvoke(state)
    agent_results = list(result.get("agent_results") or [])
    all_findings = [finding for agent_result in agent_results for finding in agent_result.findings]
    grounding = filter_grounded_findings(all_findings, pr_payload)
    grounded_result = AgentResult(
        agent="grounded",
        findings=grounding.kept,
        confidence=0.0,
        execution_time=0.0,
    )
    ranked = (ranker or CommentRanker()).rank([grounded_result])
    return ReviewPipelineResult(
        agent_results=agent_results,
        ranked_findings=ranked,
        dropped_findings_count=len(grounding.dropped),
    )
