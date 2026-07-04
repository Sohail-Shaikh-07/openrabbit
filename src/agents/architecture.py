"""Architecture review agent for OpenRabbit.

Checks PR diffs for layer violations, dependency violations, and service
boundary violations. Unlike other agents, this one is grounded in the
repository's own architecture docs retrieved from Qdrant by the context
retriever so findings are specific to the project's design decisions.
"""

from __future__ import annotations

import logging
import time

from agents.base import BaseReviewAgent
from agents.llm import LLMClient, OllamaClient, mean_confidence, parse_findings
from agents.models import AgentResult, Finding, ReviewState
from agents.prompting import (
    JSON_RESPONSE_CONTRACT,
    REVIEW_DISCIPLINE,
    collect_context,
    format_changed_line_evidence,
    format_prompt_diff,
)

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are OpenRabbit's architecture review agent. Review the pull request like a technical lead preserving system boundaries, maintainability, and long-term design clarity.

Mission:
- Identify architecture violations introduced or exposed by the changed lines.
- Treat project-specific architecture context as the source of truth.
- Distinguish real design breakage from harmless local implementation detail.
- Prefer findings that protect ownership boundaries, dependency direction, deployment isolation, or module contracts.

Architecture classes to consider:
- Layer violations: API, service, domain, data, infrastructure, or UI layers bypassing their intended boundary.
- Dependency violations: imports or calls against modules that should not know about each other.
- Service boundary violations: direct use of another service's internals instead of its public contract.
- Circular dependencies, hidden coupling, global state, or cross-cutting shortcuts.
- Changes that make future testing, migration, or local-first operation materially harder.

Architecture context:
{architecture_context}

{changed_line_evidence}

Diff:
{diff}

{review_discipline}

{json_contract}

If no architecture violations are found, return {{"findings": []}}.
"""


class ArchitectureAgent(BaseReviewAgent):
    """Review agent that surfaces architecture violations in PR diffs."""

    name = "architecture"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            diff = format_prompt_diff(state.get("pr_payload"))
            arch_context = collect_context(state, "architecture")
            changed_line_evidence = format_changed_line_evidence(state.get("pr_payload"))
            prompt = _PROMPT_TEMPLATE.format(
                architecture_context=arch_context,
                changed_line_evidence=changed_line_evidence,
                diff=diff,
                review_discipline=REVIEW_DISCIPLINE,
                json_contract=JSON_RESPONSE_CONTRACT,
            )
            raw = await self._client.generate(prompt)
            findings = parse_findings(raw, "architecture")
        except Exception:
            logger.exception("ArchitectureAgent failed to complete review")

        return AgentResult(
            agent=self.name,
            findings=findings,
            confidence=mean_confidence(findings),
            execution_time=time.monotonic() - started,
        )
