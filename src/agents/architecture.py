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
from agents.llm import OllamaClient, mean_confidence, parse_findings
from agents.models import AgentResult, Finding, ReviewState

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are an architecture code reviewer. Analyze the following pull request diff against the project's architecture rules and identify violations.

Architecture context (project-specific rules and design decisions):
{architecture_context}

Diff:
{diff}

Check specifically for:
- Layer violations (e.g. API layer importing from database layer directly)
- Dependency violations (importing modules that should not depend on each other)
- Service boundary violations (one service calling another service's internals)
- Circular dependencies introduced by this change

Reply with ONLY a JSON object in this exact format, no prose:
{{
  "findings": [
    {{
      "severity": "critical|high|medium|low",
      "file": "path/to/file.py",
      "line": 42,
      "confidence": 0.85,
      "title": "Short title",
      "reason": "Why this violates the architecture.",
      "suggestion": "How to fix it.",
      "fix": "Optional corrected import or code snippet"
    }}
  ]
}}

If no architecture violations are found, return {{"findings": []}}
"""

_NO_CONTEXT = "(No architecture context retrieved for this review.)"


class ArchitectureAgent(BaseReviewAgent):
    """Review agent that surfaces architecture violations in PR diffs."""

    name = "architecture"

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            pr = state.get("pr_payload")
            diff: str = getattr(pr, "diff", "") or "" if pr else ""
            arch_context = _extract_architecture_context(state)
            prompt = _PROMPT_TEMPLATE.format(
                architecture_context=arch_context,
                diff=diff,
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


def _extract_architecture_context(state: ReviewState) -> str:
    retrieval = state.get("retrieval_result")
    if retrieval is None:
        return _NO_CONTEXT
    docs: list[str] = getattr(retrieval, "architecture", []) or []
    if not docs:
        return _NO_CONTEXT
    return "\n\n".join(docs)
