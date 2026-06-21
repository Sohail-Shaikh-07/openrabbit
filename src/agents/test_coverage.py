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
from agents.llm import OllamaClient, mean_confidence, parse_findings
from agents.models import AgentResult, Finding, ReviewState

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are a test coverage code reviewer. Analyze the following pull request diff and identify missing or inadequate tests.

Existing test context (from the project's test suite):
{test_context}

Diff:
{diff}

Check specifically for:
- New functions or methods added without corresponding unit tests
- New classes added without test coverage
- Edge cases and error paths that are not tested
- Weak assertions (only checking that code runs, not checking output)
- Missing integration tests for new API endpoints or service methods

Reply with ONLY a JSON object in this exact format, no prose:
{{
  "findings": [
    {{
      "severity": "critical|high|medium|low",
      "file": "path/to/file.py",
      "line": 42,
      "confidence": 0.80,
      "title": "Short title",
      "reason": "Why this needs a test.",
      "suggestion": "What test to add and where.",
      "fix": "Optional example test snippet"
    }}
  ]
}}

If test coverage looks adequate, return {{"findings": []}}
"""

_NO_CONTEXT = "(No test context retrieved for this review.)"


class TestCoverageAgent(BaseReviewAgent):
    """Review agent that surfaces missing test coverage in PR diffs."""

    name = "test_coverage"

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            pr = state.get("pr_payload")
            diff: str = getattr(pr, "diff", "") or "" if pr else ""
            test_context = _extract_test_context(state)
            prompt = _PROMPT_TEMPLATE.format(
                test_context=test_context,
                diff=diff,
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


def _extract_test_context(state: ReviewState) -> str:
    retrieval = state.get("retrieval_result")
    if retrieval is None:
        return _NO_CONTEXT
    tests: list[str] = getattr(retrieval, "tests", []) or []
    if not tests:
        return _NO_CONTEXT
    return "\n\n".join(tests)
