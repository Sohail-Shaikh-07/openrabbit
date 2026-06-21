"""Security review agent for OpenRabbit.

Inspects PR diffs for common vulnerability classes:
SQL injection, hardcoded secrets, authentication bypass, XSS, CSRF, SSRF,
and path traversal. Calls the local Ollama LLM and parses its JSON output
into structured :class:`~agents.models.Finding` objects.
"""

from __future__ import annotations

import logging
import time

from agents.base import BaseReviewAgent
from agents.llm import CONFIDENCE_THRESHOLD, OllamaClient, mean_confidence, parse_findings
from agents.models import AgentResult, Finding, ReviewState

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are a security code reviewer. Analyze the following pull request diff and identify security vulnerabilities.

Check specifically for:
- SQL injection (unsanitized user input in queries)
- Hardcoded secrets, passwords, API keys, or tokens
- Authentication bypass (missing auth checks, insecure defaults)
- XSS (unescaped user-controlled output in HTML contexts)
- CSRF (missing CSRF tokens on state-changing endpoints)
- SSRF (user-controlled URLs passed to HTTP clients)
- Path traversal (user-controlled file paths)

Diff:
{diff}

Reply with ONLY a JSON object in this exact format, no prose:
{{
  "findings": [
    {{
      "severity": "critical|high|medium|low",
      "file": "path/to/file.py",
      "line": 42,
      "confidence": 0.95,
      "title": "Short title",
      "reason": "Why this is a vulnerability.",
      "suggestion": "How to fix it.",
      "fix": "Optional corrected code snippet"
    }}
  ]
}}

If no security issues are found, return {{"findings": []}}
"""


class SecurityAgent(BaseReviewAgent):
    """Review agent that surfaces security vulnerabilities in PR diffs."""

    name = "security"

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            diff = _extract_diff(state)
            prompt = _PROMPT_TEMPLATE.format(diff=diff)
            raw = await self._client.generate(prompt)
            findings = parse_findings(raw, "security")
        except Exception:
            logger.exception("SecurityAgent failed to complete review")

        return AgentResult(
            agent=self.name,
            findings=findings,
            confidence=mean_confidence(findings),
            execution_time=time.monotonic() - started,
        )


def _extract_diff(state: ReviewState) -> str:
    pr = state.get("pr_payload")
    if pr is None:
        return ""
    diff: str = getattr(pr, "diff", "") or ""
    return diff


__all__ = ["CONFIDENCE_THRESHOLD", "SecurityAgent"]
