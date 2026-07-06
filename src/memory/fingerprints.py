"""Stable fingerprints for review findings.

Fingerprints deliberately ignore severity, confidence, and most wording. The
goal is to identify the same root cause across re-runs even when a model
phrases it differently.
"""

from __future__ import annotations

import hashlib
import re

from agents.models import Finding

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def fingerprint_finding(finding: Finding) -> str:
    """Return a stable identifier for a finding's root cause."""
    parts = (
        _normalise_text(finding.category),
        _normalise_path(finding.file),
        _issue_kind(finding),
        _line_bucket(finding.line),
    )
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _normalise_path(path: str) -> str:
    clean = path.strip().replace("\\", "/").lower()
    if clean.startswith(("a/", "b/")):
        clean = clean[2:]
    return clean.lstrip("/")


def _line_bucket(line: int) -> str:
    if line <= 0:
        return "file"
    # Keep nearby model line drift together without merging unrelated files.
    return str(((line - 1) // 5) * 5 + 1)


def _normalise_text(text: str) -> str:
    return _TOKEN_RE.sub("-", text.strip().lower()).strip("-")


def _issue_kind(finding: Finding) -> str:
    text = " ".join((finding.title, finding.reason, finding.suggestion)).lower()
    if "sql" in text or "injection" in text or "raw query" in text or "raw sql" in text:
        return "sql-injection"
    if _contains_any(text, ("secret", "token", "credential", "password")):
        return "secret-exposure"
    if _contains_any(text, ("authorization", "permission", "privilege", "access control")):
        return "authorization"
    if _contains_any(text, ("null", "none", "nil")) and _contains_any(
        text, ("dereference", "attribute", "access", "guard")
    ):
        return "null-safety"
    title = _normalise_text(finding.title)
    return title or "unknown"


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
