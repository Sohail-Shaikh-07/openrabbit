"""Bug detection agent for OpenRabbit.

Checks PR diffs for logic bugs and correctness issues:
null dereference, logic errors, boundary conditions, race conditions,
missing error handling, and exception safety gaps.
Reuses the shared OllamaClient and parsing helpers from agents.llm.
"""

from __future__ import annotations

import logging
import time

from agents.base import BaseReviewAgent
from agents.llm import OllamaClient, mean_confidence, parse_findings
from agents.models import AgentResult, Finding, ReviewState

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are a bug detection code reviewer. Analyze the following pull request diff and identify correctness issues and bugs.

Check specifically for:
- Null or None dereference (accessing attributes or calling methods on values that may be None)
- Logic errors (wrong operator, inverted condition, incorrect algorithm)
- Boundary conditions (off-by-one errors, empty collection handling, integer overflow)
- Race conditions (shared mutable state accessed from multiple threads without locking)
- Missing error handling (unhandled exceptions, unchecked return values)
- Exception safety (resources not cleaned up on error, missing finally/context manager)

Diff:
{diff}

Reply with ONLY a JSON object in this exact format, no prose:
{{
  "findings": [
    {{
      "severity": "critical|high|medium|low",
      "file": "path/to/file.py",
      "line": 42,
      "confidence": 0.85,
      "title": "Short title",
      "reason": "Why this is a bug.",
      "suggestion": "How to fix it.",
      "fix": "Optional corrected code snippet"
    }}
  ]
}}

If no bugs are found, return {{"findings": []}}
"""


class BugDetectionAgent(BaseReviewAgent):
    """Review agent that surfaces bugs and logic errors in PR diffs."""

    name = "bug_detection"

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            pr = state.get("pr_payload")
            diff: str = getattr(pr, "diff", "") or "" if pr else ""
            prompt = _PROMPT_TEMPLATE.format(diff=diff)
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
