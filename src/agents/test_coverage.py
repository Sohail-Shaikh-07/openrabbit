"""Test coverage agent for OpenRabbit.

Identifies new functions and classes introduced by a PR that lack
corresponding unit tests, weak assertions, and coverage gaps.
Uses retrieved test context from Qdrant to understand existing test patterns
so findings are grounded in the project's conventions.
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

_PROMPT_TEMPLATE = """You are OpenRabbit's test coverage review agent. Review the pull request like a senior maintainer deciding whether the changed behavior is protected by useful tests.

Mission:
- Identify important behavior introduced or changed by the diff that lacks meaningful tests.
- Use existing test context to recommend the right file, style, and assertion level.
- Focus on user-visible behavior, edge cases, regression risks, error paths, and integration boundaries.
- Avoid asking for tests when the diff is purely documentation, generated code, or already covered by nearby tests.

Coverage classes to consider:
- New public functions, commands, API endpoints, config behavior, model adapters, or integration logic.
- Changed error handling, validation, branching, permissions, serialization, retries, or persistence.
- Missing regression coverage for bug fixes.
- Weak assertions that only check that code runs rather than checking behavior.
- Missing integration tests when unit tests cannot prove the cross-module contract.

Existing test context:
{test_context}

{changed_line_evidence}

Diff:
{diff}

{review_discipline}

{json_contract}

If test coverage looks adequate, return {{"findings": []}}.
"""


class TestCoverageAgent(BaseReviewAgent):
    """Review agent that surfaces missing test coverage in PR diffs."""

    name = "test_coverage"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            diff = format_prompt_diff(state.get("pr_payload"))
            test_context = collect_context(state, "tests")
            changed_line_evidence = format_changed_line_evidence(state.get("pr_payload"))
            prompt = _PROMPT_TEMPLATE.format(
                test_context=test_context,
                changed_line_evidence=changed_line_evidence,
                diff=diff,
                review_discipline=REVIEW_DISCIPLINE,
                json_contract=JSON_RESPONSE_CONTRACT,
            )
            raw = await self._client.generate(prompt)
            findings = parse_findings(raw, "tests")
        except Exception:
            logger.exception("TestCoverageAgent failed to complete review")

        return AgentResult(
            agent=self.name,
            findings=findings,
            confidence=mean_confidence(findings),
            execution_time=time.monotonic() - started,
        )
