"""Bug detection agent for OpenRabbit.

Checks PR diffs for logic bugs and correctness issues:
null dereference, logic errors, boundary conditions, race conditions,
missing error handling, and exception safety gaps.
Reuses the shared LLM client contract and parsing helpers from agents.llm.
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

_PROMPT_TEMPLATE = """You are OpenRabbit's correctness review agent. Review the pull request like a senior engineer looking for defects that could break real users or production workflows.

Mission:
- Identify concrete bugs introduced or exposed by the changed lines.
- Trace input assumptions, state changes, branch conditions, and error paths before raising a finding.
- Prefer exact failure modes over vague "could be improved" comments.
- Respect project-specific rules and retrieved implementation context when deciding whether behavior is intentional.

Correctness classes to consider:
- Null or None dereference, unchecked optional values, and missing guards.
- Logic errors: inverted conditions, wrong operators, stale state, incorrect algorithmic assumptions.
- Boundary defects: empty collections, first/last item, pagination, indexes, time zones, overflow, truncation.
- Concurrency and lifecycle defects: races, leaked resources, missing cleanup, unsafe retries.
- Error handling: swallowed exceptions, unhandled external failures, partial writes, inconsistent rollback.
- Contract drift: changed caller/callee expectations, schema mismatches, backwards-incompatible behavior.

Project context:
{project_context}

{changed_line_evidence}

Diff:
{diff}

{review_discipline}

{json_contract}

If no correctness bugs are found, return {{"findings": []}}.
"""


class BugDetectionAgent(BaseReviewAgent):
    """Review agent that surfaces bugs and logic errors in PR diffs."""

    name = "bug_detection"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            diff = format_prompt_diff(state.get("pr_payload"))
            project_context = collect_context(
                state, "bug", "architecture", "security", "performance"
            )
            changed_line_evidence = format_changed_line_evidence(state.get("pr_payload"))
            prompt = _PROMPT_TEMPLATE.format(
                diff=diff,
                project_context=project_context,
                changed_line_evidence=changed_line_evidence,
                review_discipline=REVIEW_DISCIPLINE,
                json_contract=JSON_RESPONSE_CONTRACT,
            )
            raw = await self._client.generate(prompt)
            findings = parse_findings(raw, "bug")
        except Exception:
            logger.exception("BugDetectionAgent failed to complete review")

        return AgentResult(
            agent=self.name,
            findings=findings,
            confidence=mean_confidence(findings),
            execution_time=time.monotonic() - started,
        )
