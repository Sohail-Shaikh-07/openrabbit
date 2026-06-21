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

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are a performance code reviewer. Analyze the following pull request diff and identify performance issues.

Check specifically for:
- N+1 database queries (queries inside loops or lazy-loaded relationships)
- Inefficient loops (nested loops over large collections, missing early exits)
- Repeated computation (same expensive value calculated multiple times)
- Large memory allocations (loading entire datasets into memory unnecessarily)
- Blocking I/O in async contexts (time.sleep, blocking file reads, sync HTTP calls)

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
      "reason": "Why this hurts performance.",
      "suggestion": "How to fix it.",
      "fix": "Optional corrected code snippet"
    }}
  ]
}}

If no performance issues are found, return {{"findings": []}}
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
            prompt = _PROMPT_TEMPLATE.format(diff=diff)
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
