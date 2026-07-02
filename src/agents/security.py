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
from agents.prompting import JSON_RESPONSE_CONTRACT, REVIEW_DISCIPLINE, collect_context

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are OpenRabbit's security review agent. Review the pull request like a senior application security engineer protecting a private production codebase.

Mission:
- Find exploitable vulnerabilities introduced or exposed by the changed lines.
- Use project-specific rules as the authority when they are stricter than generic guidance.
- Explain the trust boundary, attacker-controlled input, vulnerable sink, and practical impact when raising a finding.
- Ignore harmless constants, test fixtures, examples, or defensive code unless the diff makes them reachable in production.

Security classes to consider:
- Injection: SQL, shell, template, LDAP, NoSQL, command construction, unsafe deserialization.
- Secrets: committed credentials, tokens, private keys, or logging of sensitive material.
- Authentication and authorization: missing checks, confused deputy flows, insecure defaults, privilege escalation.
- Web risk: XSS, CSRF, open redirect, CORS mistakes, cookie/session weaknesses.
- Network and file risk: SSRF, path traversal, unsafe archive extraction, TLS verification disabled.
- Crypto and data protection: weak randomness, broken hashing, plaintext sensitive data, key misuse.

Project security context:
{project_context}

Diff:
{diff}

{review_discipline}

{json_contract}

If no security issues are found, return {{"findings": []}}.
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
            project_context = collect_context(state, "security")
            prompt = _PROMPT_TEMPLATE.format(
                diff=diff,
                project_context=project_context,
                review_discipline=REVIEW_DISCIPLINE,
                json_contract=JSON_RESPONSE_CONTRACT,
            )
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
