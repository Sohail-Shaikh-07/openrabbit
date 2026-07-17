"""Tests for CommentRanker."""

from __future__ import annotations

import pytest

from agents.models import AgentResult, Finding, Severity
from ranking.ranker import CommentRanker, RankedFinding


def _finding(
    title: str = "Issue",
    severity: Severity = Severity.medium,
    confidence: float = 0.80,
    file: str = "app.py",
    line: int = 10,
    category: str = "security",
    reason: str = "reason",
    suggestion: str = "suggestion",
) -> Finding:
    return Finding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        confidence=confidence,
        title=title,
        reason=reason,
        suggestion=suggestion,
        fix="",
    )


def _result(agent: str, findings: list[Finding]) -> AgentResult:
    return AgentResult(
        agent=agent,
        findings=findings,
        confidence=0.8,
        execution_time=0.1,
    )


# ---------------------------------------------------------------------------
# RankedFinding
# ---------------------------------------------------------------------------


def test_ranked_finding_stores_finding_and_score() -> None:
    f = _finding()
    rf = RankedFinding(finding=f, score=0.95)
    assert rf.finding is f
    assert rf.score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# CommentRanker.rank
# ---------------------------------------------------------------------------


def test_ranker_returns_empty_for_no_results() -> None:
    ranker = CommentRanker()
    assert ranker.rank([]) == []


def test_ranker_returns_all_findings_from_single_agent() -> None:
    findings = [_finding("A"), _finding("B")]
    results = [_result("security", findings)]
    ranked = CommentRanker().rank(results)
    assert len(ranked) == 2


def test_ranker_orders_by_score_descending() -> None:
    low = _finding("low", severity=Severity.low, confidence=0.75)
    high = _finding("high", severity=Severity.critical, confidence=0.95)
    results = [_result("security", [low, high])]
    ranked = CommentRanker().rank(results)
    assert ranked[0].finding.title == "high"
    assert ranked[1].finding.title == "low"


def test_ranker_caps_at_top_n() -> None:
    findings = [_finding(f"issue-{i}", confidence=0.80) for i in range(15)]
    results = [_result("security", findings)]
    ranked = CommentRanker(top_n=5).rank(results)
    assert len(ranked) == 5


def test_ranker_deduplicates_same_file_line_title() -> None:
    f1 = _finding("SQL injection", file="db.py", line=5)
    f2 = _finding("SQL injection", file="db.py", line=5)
    results = [_result("security", [f1]), _result("bug", [f2])]
    ranked = CommentRanker().rank(results)
    assert len(ranked) == 1


def test_ranker_deduplicates_same_root_cause_from_multiple_agents() -> None:
    security = _finding(
        "SQL Injection vulnerability in advanced_search method",
        severity=Severity.critical,
        confidence=0.95,
        file="app/repositories/task_repository.py",
        line=74,
        category="security",
    )
    performance = _finding(
        "Potential SQL injection and inefficient query construction in advanced_search",
        severity=Severity.high,
        confidence=0.90,
        file="app/repositories/task_repository.py",
        line=73,
        category="performance",
    )
    architecture = _finding(
        "SQL Injection Risk and Layer Violation in Repository Advanced Search",
        severity=Severity.high,
        confidence=0.88,
        file="app/repositories/task_repository.py",
        line=73,
        category="architecture",
    )
    tests = _finding(
        "Insufficient test coverage for advanced search edge cases",
        severity=Severity.medium,
        confidence=0.85,
        file="tests/test_task_search.py",
        line=4,
        category="tests",
    )

    ranked = CommentRanker().rank(
        [
            _result("security", [security]),
            _result("performance", [performance]),
            _result("architecture", [architecture]),
            _result("tests", [tests]),
        ]
    )

    assert [item.finding.title for item in ranked] == [
        "SQL Injection vulnerability in advanced_search method",
        "Insufficient test coverage for advanced search edge cases",
    ]


def test_ranker_deduplicates_repeated_authorization_findings_same_line() -> None:
    first = _finding(
        "Missing authorization check on admin task reassignment endpoint",
        severity=Severity.high,
        confidence=0.92,
        file="app/api/routes/tasks.py",
        line=70,
        category="security",
    )
    second = _finding(
        "Missing explicit authorization check before sensitive admin task reassignment",
        severity=Severity.high,
        confidence=0.90,
        file="app/api/routes/tasks.py",
        line=70,
        category="architecture",
    )

    ranked = CommentRanker().rank(
        [
            _result("security", [first]),
            _result("architecture", [second]),
        ]
    )

    assert len(ranked) == 1
    assert ranked[0].finding.title == first.title


def test_ranker_deduplicates_audit_trail_findings_in_same_file() -> None:
    first = _finding(
        "Missing audit logging of reassignment reason",
        severity=Severity.medium,
        confidence=0.89,
        file="app/api/routes/tasks.py",
        line=24,
        category="security",
    )
    second = _finding(
        "Reassignment reason is collected but not logged or stored",
        severity=Severity.medium,
        confidence=0.88,
        file="app/api/routes/tasks.py",
        line=81,
        category="architecture",
    )

    ranked = CommentRanker().rank(
        [
            _result("security", [first]),
            _result("architecture", [second]),
        ]
    )

    assert len(ranked) == 1
    assert ranked[0].finding.title == first.title


def test_ranker_prefers_audit_kind_when_audit_finding_mentions_admin() -> None:
    first = _finding(
        "Missing audit logging or persistence of reassignment reason",
        severity=Severity.medium,
        confidence=0.89,
        file="app/api/routes/tasks.py",
        line=81,
        category="security",
        reason=(
            "The AdminReassignmentRequest collects a reason field, but this reason is "
            "neither logged nor stored. This breaks the audit trail contract expected "
            "for sensitive admin operations."
        ),
    )
    second = _finding(
        "Missing audit logging of reassignment reason",
        severity=Severity.medium,
        confidence=0.88,
        file="app/api/routes/tasks.py",
        line=70,
        category="performance",
        reason=(
            "The reassignment reason is collected in the payload but not logged or "
            "stored, which may lead to lack of traceability and auditing."
        ),
    )

    ranked = CommentRanker().rank(
        [
            _result("security", [first]),
            _result("performance", [second]),
        ]
    )

    assert len(ranked) == 1
    assert ranked[0].finding.title == first.title


def test_ranker_deduplicates_export_pagination_completeness_findings() -> None:
    first = _finding(
        "Fixed pagination parameters limit export completeness",
        severity=Severity.medium,
        confidence=0.90,
        file="app/api/routes/tasks.py",
        line=74,
        category="performance",
        reason=(
            "The endpoint calls list_tasks with a fixed limit=100 and offset=0, "
            "which may truncate exported results when more than 100 tasks match."
        ),
    )
    second = _finding(
        "Hardcoded pagination limit may limit export completeness",
        severity=Severity.medium,
        confidence=0.89,
        file="app/api/routes/tasks.py",
        line=74,
        category="architecture",
        reason=(
            "The export endpoint has a hardcoded limit of 100 with offset 0, "
            "which can return incomplete export data for larger task sets."
        ),
    )

    ranked = CommentRanker().rank(
        [
            _result("performance", [first]),
            _result("architecture", [second]),
        ]
    )

    assert len(ranked) == 1
    assert ranked[0].finding.title == first.title


def test_ranker_keeps_different_lines_as_separate_findings() -> None:
    f1 = _finding("SQL injection", file="db.py", line=5)
    f2 = _finding("SQL injection", file="db.py", line=20)
    results = [_result("security", [f1, f2])]
    ranked = CommentRanker().rank(results)
    assert len(ranked) == 2


def test_ranker_score_increases_with_severity() -> None:
    low = _finding("A", severity=Severity.low, confidence=0.80)
    critical = _finding("B", severity=Severity.critical, confidence=0.80)
    results = [_result("security", [low, critical])]
    ranked = CommentRanker().rank(results)
    scores = {r.finding.title: r.score for r in ranked}
    assert scores["B"] > scores["A"]


def test_ranker_score_increases_with_confidence() -> None:
    lo_conf = _finding("A", severity=Severity.high, confidence=0.71)
    hi_conf = _finding("B", severity=Severity.high, confidence=0.99)
    results = [_result("security", [lo_conf, hi_conf])]
    ranked = CommentRanker().rank(results)
    scores = {r.finding.title: r.score for r in ranked}
    assert scores["B"] > scores["A"]


def test_ranker_merges_findings_from_multiple_agents() -> None:
    sec = [_finding("SQL injection", category="security")]
    perf = [_finding("N+1 query", category="performance")]
    results = [_result("security", sec), _result("performance", perf)]
    ranked = CommentRanker().rank(results)
    categories = {r.finding.category for r in ranked}
    assert "security" in categories
    assert "performance" in categories
