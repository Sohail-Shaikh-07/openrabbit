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
from agents.llm import (
    CONFIDENCE_THRESHOLD,
    LLMClient,
    OllamaClient,
    mean_confidence,
    parse_findings,
)
from agents.models import AgentResult, Finding, ReviewState, Severity
from agents.prompting import (
    JSON_RESPONSE_CONTRACT,
    REVIEW_DISCIPLINE,
    collect_context,
    format_changed_line_evidence,
)

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

Security review rules:
- Authentication-only checks do not prove authorization. For changed routes or handlers named admin, manage, reassign, delete, role, permission, or invite, verify there is an explicit role or privilege check before sensitive state changes.
- Do not label plain assignment as SQL injection. Pydantic validation, ORM attribute updates, and JSON serialization are not injection sinks unless the changed value reaches a raw SQL string, text(), execute(), shell command, or equivalent interpreter sink.
- For injection findings, identify both the attacker-controlled source and the changed sink line.

Project security context:
{project_context}

{changed_line_evidence}

Diff:
{diff}

{review_discipline}

{json_contract}

If no security issues are found, return {{"findings": []}}.
"""


class SecurityAgent(BaseReviewAgent):
    """Review agent that surfaces security vulnerabilities in PR diffs."""

    name = "security"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or OllamaClient()

    async def run(self, state: ReviewState) -> AgentResult:
        started = time.monotonic()
        findings: list[Finding] = []

        try:
            diff = _extract_diff(state)
            project_context = collect_context(state, "security")
            changed_line_evidence = format_changed_line_evidence(state.get("pr_payload"))
            prompt = _PROMPT_TEMPLATE.format(
                diff=diff,
                project_context=project_context,
                changed_line_evidence=changed_line_evidence,
                review_discipline=REVIEW_DISCIPLINE,
                json_contract=JSON_RESPONSE_CONTRACT,
            )
            raw = await self._client.generate(prompt)
            model_findings = parse_findings(raw, "security")
            model_findings = _filter_unsupported_model_findings(
                model_findings,
                state.get("pr_payload"),
            )
            model_findings = _deduplicate_security_findings(model_findings)
            preflight_findings = _detect_preflight_findings(state.get("pr_payload"))
            findings = _deduplicate_security_findings(
                _merge_preflight_findings(model_findings, preflight_findings)
            )
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


def _detect_preflight_findings(pr_payload: object) -> list[Finding]:
    findings: list[Finding] = []
    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list):
        return findings

    for file_ in files:
        if bool(getattr(file_, "is_binary", False)):
            continue

        changed_lines = _changed_lines(file_)
        raw_sql = _first_raw_sql_line(changed_lines)
        if raw_sql is not None:
            line_number, _ = raw_sql
            findings.append(
                Finding(
                    severity=Severity.high,
                    category="security",
                    file=_file_path(file_),
                    line=line_number,
                    confidence=0.92,
                    title="Raw SQL construction from changed input",
                    reason=(
                        "The changed line builds raw SQL with string interpolation. "
                        "Attacker-controlled input can change query structure."
                    ),
                    suggestion="Use SQLAlchemy expressions or bound parameters instead of interpolated SQL.",
                    fix="",
                )
            )

        admin_route = _admin_route_without_authorization(changed_lines)
        if admin_route is not None:
            line_number, _ = admin_route
            findings.append(
                Finding(
                    severity=Severity.high,
                    category="security",
                    file=_file_path(file_),
                    line=line_number,
                    confidence=0.90,
                    title="Admin route lacks an authorization check",
                    reason=(
                        "The changed admin-style route only proves the caller is authenticated. "
                        "It does not verify an admin role or privilege before mutating task ownership."
                    ),
                    suggestion="Require an explicit admin, role, permission, or scope check before the mutation.",
                    fix="",
                )
            )

    return findings


def _changed_lines(file_: object) -> list[tuple[int, str]]:
    additions: list[tuple[int, str]] = []
    hunks = getattr(file_, "hunks", None)
    if not isinstance(hunks, list):
        return additions

    for hunk in hunks:
        new_line = int(getattr(hunk, "new_start", 0) or 0)
        hunk_lines = getattr(hunk, "lines", None)
        if not isinstance(hunk_lines, list):
            continue

        for line in hunk_lines:
            kind = getattr(line, "kind", "")
            text = str(getattr(line, "text", ""))
            if kind == "addition":
                additions.append((new_line, text))
                new_line += 1
            elif kind == "context":
                new_line += 1

    return additions


def _first_raw_sql_line(changed_lines: list[tuple[int, str]]) -> tuple[int, str] | None:
    for line_number, text in changed_lines:
        lowered = text.lower()
        if ('text(f"' in lowered or "text(f'" in lowered) and _contains_sql_keyword(lowered):
            return line_number, text
        if ('.execute(f"' in lowered or ".execute(f'" in lowered) and _contains_sql_keyword(
            lowered
        ):
            return line_number, text
    return None


def _contains_sql_keyword(text: str) -> bool:
    return any(keyword in text for keyword in ("select ", "insert ", "update ", "delete "))


def _admin_route_without_authorization(
    changed_lines: list[tuple[int, str]],
) -> tuple[int, str] | None:
    route_line: tuple[int, str] | None = None
    sensitive = False
    has_authorization = False

    for line_number, text in changed_lines:
        lowered = text.lower()
        if "@router." in lowered and (
            '"/admin' in lowered or "'/admin" in lowered or "reassign" in lowered
        ):
            route_line = (line_number, text)
            sensitive = True
        if lowered.strip().startswith("def admin_") or " reassign" in lowered:
            sensitive = True
        if any(
            marker in lowered
            for marker in (
                "require_admin",
                "admin_required",
                "is_admin",
                "has_role",
                "has_permission",
                "authorize",
                "permission",
                "scope",
                "superuser",
                "staff",
            )
        ):
            has_authorization = True

    if sensitive and not has_authorization:
        return route_line or changed_lines[0] if changed_lines else None
    return None


def _file_path(file_: object) -> str:
    return str(getattr(file_, "path", "") or getattr(getattr(file_, "file", None), "filename", ""))


def _merge_preflight_findings(
    model_findings: list[Finding],
    preflight_findings: list[Finding],
) -> list[Finding]:
    merged = list(model_findings)
    for preflight in preflight_findings:
        if any(_same_security_issue(preflight, model) for model in model_findings):
            continue
        merged.append(preflight)
    return merged


def _deduplicate_security_findings(findings: list[Finding]) -> list[Finding]:
    deduped: list[Finding] = []
    for finding in findings:
        if any(_same_security_issue(finding, existing) for existing in deduped):
            continue
        deduped.append(finding)
    return deduped


def _filter_unsupported_model_findings(
    findings: list[Finding],
    pr_payload: object,
) -> list[Finding]:
    changed_by_file = _changed_lines_by_file(pr_payload)
    if not changed_by_file:
        return findings

    supported: list[Finding] = []
    for finding in findings:
        if _issue_kind(finding) == "sql_injection":
            changed_lines = changed_by_file.get(finding.file, [])
            if changed_lines and not _has_raw_sql_sink_near(changed_lines, finding.line):
                continue
        supported.append(finding)
    return supported


def _changed_lines_by_file(pr_payload: object) -> dict[str, list[tuple[int, str]]]:
    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list):
        return {}

    result: dict[str, list[tuple[int, str]]] = {}
    for file_ in files:
        if bool(getattr(file_, "is_binary", False)):
            continue
        result[_file_path(file_)] = _changed_lines(file_)
    return result


def _has_raw_sql_sink_near(changed_lines: list[tuple[int, str]], line_number: int) -> bool:
    for changed_line_number, text in changed_lines:
        if abs(changed_line_number - line_number) > 15:
            continue
        if _first_raw_sql_line([(changed_line_number, text)]) is not None:
            return True
    return False


def _same_security_issue(a: Finding, b: Finding) -> bool:
    if a.file != b.file:
        return False
    if abs(a.line - b.line) > 15:
        return False
    return _issue_kind(a) == _issue_kind(b)


def _issue_kind(finding: Finding) -> str:
    text = f"{finding.title} {finding.reason} {finding.suggestion}".lower()
    if "sql" in text or "injection" in text or "raw query" in text:
        return "sql_injection"
    if "admin" in text or "authorization" in text or "permission" in text or "privilege" in text:
        return "admin_authorization"
    return text[:80]


__all__ = ["CONFIDENCE_THRESHOLD", "SecurityAgent"]
