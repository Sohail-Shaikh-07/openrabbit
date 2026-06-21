"""Security review agent for OpenRabbit.

Inspects PR diffs for common vulnerability classes:
SQL injection, hardcoded secrets, authentication bypass, XSS, CSRF, SSRF,
and path traversal. Calls the local Ollama LLM and parses its JSON output
into structured :class:`~agents.models.Finding` objects.
"""

from __future__ import annotations

import json
import logging
import time

from agents.base import BaseReviewAgent
from agents.llm import OllamaClient
from agents.models import AgentResult, Finding, ReviewState, Severity

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.70

_SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.critical,
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
}

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
            findings = _parse_findings(raw, "security")
        except Exception:
            logger.exception("SecurityAgent failed to complete review")

        return AgentResult(
            agent=self.name,
            findings=findings,
            confidence=_mean_confidence(findings),
            execution_time=time.monotonic() - started,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_diff(state: ReviewState) -> str:
    pr = state.get("pr_payload")
    if pr is None:
        return ""
    diff: str = getattr(pr, "diff", "") or ""
    return diff


def _parse_findings(raw: str, category: str) -> list[Finding]:
    """Parse LLM JSON output into Finding objects, filtering by confidence."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("SecurityAgent: could not parse LLM response as JSON")
        return []

    results: list[Finding] = []
    for item in data.get("findings", []):
        try:
            confidence = float(item.get("confidence", 0.0))
            if confidence < CONFIDENCE_THRESHOLD:
                continue
            severity = _SEVERITY_MAP.get(str(item.get("severity", "low")).lower(), Severity.low)
            results.append(
                Finding(
                    severity=severity,
                    category=category,
                    file=str(item.get("file", "")),
                    line=int(item.get("line", 0)),
                    confidence=confidence,
                    title=str(item.get("title", "")),
                    reason=str(item.get("reason", "")),
                    suggestion=str(item.get("suggestion", "")),
                    fix=str(item.get("fix", "")),
                )
            )
        except (TypeError, ValueError):
            logger.warning("SecurityAgent: skipping malformed finding: %s", item)

    return results


def _mean_confidence(findings: list[Finding]) -> float:
    if not findings:
        return 0.0
    return sum(f.confidence for f in findings) / len(findings)
