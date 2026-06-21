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
) -> Finding:
    return Finding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        confidence=confidence,
        title=title,
        reason="reason",
        suggestion="suggestion",
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
