"""Tests for stable finding fingerprints."""

from __future__ import annotations

from agents.models import Finding, Severity
from memory.fingerprints import fingerprint_finding


def _finding(**overrides: object) -> Finding:
    values = {
        "severity": Severity.high,
        "category": "security",
        "file": "app/repositories/task_repository.py",
        "line": 74,
        "confidence": 0.91,
        "title": "SQL Injection vulnerability in advanced_search method",
        "reason": "Raw SQL is built from user input.",
        "suggestion": "Use SQLAlchemy bind parameters.",
        "fix": "",
    }
    values.update(overrides)
    return Finding(**values)  # type: ignore[arg-type]


def test_fingerprint_is_stable_across_severity_and_wording_noise() -> None:
    first = _finding()
    second = _finding(
        severity=Severity.critical,
        confidence=0.72,
        title="Potential SQL injection and inefficient query construction",
        reason="The query string interpolates request parameters into raw SQL.",
        suggestion="Bind parameters instead of interpolating values.",
    )

    assert fingerprint_finding(first) == fingerprint_finding(second)


def test_fingerprint_is_stable_across_category_drift_for_same_root_cause() -> None:
    first = _finding(
        category="security",
        title="SQL Injection via Unsanitized User Input in advanced_search",
        reason="Raw SQL is built from user-controlled query parameters.",
    )
    second = _finding(
        category="architecture",
        title="SQL Injection Risk and Layer Violation in Repository Advanced Search",
        reason="The repository constructs raw SQL from query and owner values.",
    )

    assert fingerprint_finding(first) == fingerprint_finding(second)


def test_fingerprint_changes_for_different_file_or_issue_kind() -> None:
    base = _finding()
    different_file = _finding(file="app/services/task_service.py")
    different_issue = _finding(
        category="bug",
        title="Missing None guard",
        reason="The value can be None before attribute access.",
        suggestion="Add a guard.",
    )

    assert fingerprint_finding(base) != fingerprint_finding(different_file)
    assert fingerprint_finding(base) != fingerprint_finding(different_issue)
