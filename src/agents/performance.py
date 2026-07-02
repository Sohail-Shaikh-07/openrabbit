"""Performance review agent for OpenRabbit.

Checks PR diffs for common performance anti-patterns:
N+1 database queries, inefficient loops, repeated computation,
large memory allocations, and blocking I/O in async contexts.
Reuses the shared OllamaClient and parsing helpers from agents.llm.
"""

from __future__ import annotations

import logging
import time

from agents.base import BaseReviewAgent
from agents.llm import OllamaClient, mean_confidence, parse_findings
from agents.models import AgentResult, Finding, ReviewState
from agents.prompting import JSON_RESPONSE_CONTRACT, REVIEW_DISCIPLINE, collect_context

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are OpenRabbit's performance review agent. Review the pull request like a senior engineer responsible for latency, throughput, cost, and resource safety.

Mission:
- Find performance regressions with concrete runtime impact introduced or exposed by the changed lines.
- Estimate why the issue matters: extra queries, asymptotic growth, blocking behavior, memory growth, or repeated expensive work.
- Use project context to avoid flagging code that is already batched, cached, bounded, or intentionally off the hot path.
- Prefer no finding over speculative micro-optimization.

Performance classes to consider:
- N+1 queries, network calls in loops, or repeated remote calls.
- Algorithmic regressions: quadratic work over unbounded inputs, missing early exits, unnecessary full scans.
- Repeated expensive computation or serialization that should be cached or moved.
- Large memory allocations, loading full datasets, buffering streams, unbounded collections.
- Blocking I/O in async paths, sync clients inside event loops, avoidable lock contention.
- Resource leaks that increase latency, memory, file descriptors, or connection usage over time.

Project performance context:
{project_context}

Diff:
{diff}

{review_discipline}

{json_contract}

If no meaningful performance issues are found, return {{"findings": []}}.
"""


class PerformanceAgent(BaseReviewAgent):
    """Review agent that surfaces performance issues in PR diffs."""

    name = "performance"

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            pr = state.get("pr_payload")
            diff: str = getattr(pr, "diff", "") or "" if pr else ""
            project_context = collect_context(state, "performance")
            prompt = _PROMPT_TEMPLATE.format(
                diff=diff,
                project_context=project_context,
                review_discipline=REVIEW_DISCIPLINE,
                json_contract=JSON_RESPONSE_CONTRACT,
            )
            raw = await self._client.generate(prompt)
            findings = parse_findings(raw, "performance")
        except Exception:
            logger.exception("PerformanceAgent failed to complete review")

        return AgentResult(
            agent=self.name,
            findings=findings,
            confidence=mean_confidence(findings),
            execution_time=time.monotonic() - started,
        )
