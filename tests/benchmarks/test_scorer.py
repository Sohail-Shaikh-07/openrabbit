"""Tests for the benchmark scorer (OP-32).

Precision/recall/F1 are computed by matching ranked findings against the
known_issues list in each BenchmarkCase. Tests use hand-built BenchmarkResult
objects -- no agents, no Ollama, no network required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.models import Finding
from benchmarks.schema import BenchmarkCase, BenchmarkResult
from benchmarks.scorer import BenchmarkScorer, CaseScore, ScoredReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ranked_finding(title: str, reason: str = "") -> MagicMock:
    """Return a mock RankedFinding with the given title and reason."""
    f = MagicMock(spec=Finding)
    f.title = title
    f.reason = reason
    ranked = MagicMock()
    ranked.finding = f
    return ranked


def _make_result(
    case_id: str,
    finding_titles: list[str],
    known_issues: list[str],
    latency_ms: float = 50.0,
) -> tuple[BenchmarkResult, BenchmarkCase]:
    ranked = [_make_ranked_finding(t) for t in finding_titles]
    result = BenchmarkResult(
        case_id=case_id,
        findings=ranked,
        agent_results=[],
        latency_ms=latency_ms,
    )
    case = BenchmarkCase(
        case_id=case_id,
        diff="--- a/f.py\n+++ b/f.py\n",
        known_issues=known_issues,
    )
    return result, case


# ---------------------------------------------------------------------------
# CaseScore
# ---------------------------------------------------------------------------


def test_case_score_fields() -> None:
    score = CaseScore(case_id="c1", precision=1.0, recall=1.0, f1=1.0, tp=2, fp=0, fn=0)
    assert score.case_id == "c1"
    assert score.precision == pytest.approx(1.0)
    assert score.recall == pytest.approx(1.0)
    assert score.f1 == pytest.approx(1.0)
    assert score.tp == 2
    assert score.fp == 0
    assert score.fn == 0


def test_case_score_is_frozen() -> None:
    score = CaseScore(case_id="c1", precision=1.0, recall=0.5, f1=0.67, tp=1, fp=0, fn=1)
    with pytest.raises((AttributeError, TypeError)):
        score.precision = 0.0  # type: ignore[misc]


def test_case_score_to_dict_keys() -> None:
    score = CaseScore(case_id="c1", precision=1.0, recall=1.0, f1=1.0, tp=1, fp=0, fn=0)
    d = score.to_dict()
    assert "case_id" in d
    assert "precision" in d
    assert "recall" in d
    assert "f1" in d
    assert "tp" in d
    assert "fp" in d
    assert "fn" in d


# ---------------------------------------------------------------------------
# BenchmarkScorer.score_case()
# ---------------------------------------------------------------------------


def test_perfect_match() -> None:
    result, case = _make_result(
        "c1",
        finding_titles=["SQL injection vulnerability"],
        known_issues=["sql injection"],
    )
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 1
    assert score.fp == 0
    assert score.fn == 0
    assert score.precision == pytest.approx(1.0)
    assert score.recall == pytest.approx(1.0)


def test_false_positive() -> None:
    result, case = _make_result(
        "c1",
        finding_titles=["Unused import"],
        known_issues=["sql injection"],
    )
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 0
    assert score.fp == 1
    assert score.fn == 1
    assert score.precision == pytest.approx(0.0)
    assert score.recall == pytest.approx(0.0)


def test_false_negative() -> None:
    result, case = _make_result(
        "c1",
        finding_titles=[],
        known_issues=["sql injection", "XSS vulnerability"],
    )
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 0
    assert score.fn == 2
    assert score.recall == pytest.approx(0.0)


def test_partial_match() -> None:
    result, case = _make_result(
        "c1",
        finding_titles=["SQL injection risk", "Unreachable code"],
        known_issues=["sql injection"],
    )
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 1
    assert score.fp == 1
    assert score.fn == 0
    assert score.precision == pytest.approx(0.5)
    assert score.recall == pytest.approx(1.0)


def test_case_insensitive_matching() -> None:
    result, case = _make_result(
        "c1",
        finding_titles=["SQL INJECTION FOUND"],
        known_issues=["sql injection"],
    )
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 1


def test_reason_field_also_matched() -> None:
    ranked = [
        _make_ranked_finding(
            title="Code style issue", reason="possible sql injection via user input"
        )
    ]
    result = BenchmarkResult(case_id="c1", findings=ranked, agent_results=[], latency_ms=10.0)
    case = BenchmarkCase(case_id="c1", diff="", known_issues=["sql injection"])
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 1


def test_no_findings_no_known_issues() -> None:
    result, case = _make_result("c1", finding_titles=[], known_issues=[])
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.precision == pytest.approx(1.0)
    assert score.recall == pytest.approx(1.0)
    assert score.f1 == pytest.approx(1.0)


def test_no_known_issues_but_has_findings() -> None:
    result, case = _make_result("c1", finding_titles=["Unused import"], known_issues=[])
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 0
    assert score.fp == 1
    assert score.fn == 0
    assert score.precision == pytest.approx(0.0)
    assert score.recall == pytest.approx(1.0)


def test_f1_harmonic_mean() -> None:
    result, case = _make_result(
        "c1",
        finding_titles=["SQL injection risk", "Extra finding"],
        known_issues=["sql injection"],
    )
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    # precision=0.5 recall=1.0 -> f1=2*0.5*1.0/(0.5+1.0)=0.667
    assert score.f1 == pytest.approx(2 * 0.5 * 1.0 / (0.5 + 1.0), rel=1e-3)


def test_multiple_known_issues_all_matched() -> None:
    result, case = _make_result(
        "c1",
        finding_titles=["SQL injection issue", "XSS vulnerability found"],
        known_issues=["sql injection", "xss"],
    )
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.tp == 2
    assert score.fn == 0
    assert score.recall == pytest.approx(1.0)


def test_case_id_preserved_in_score() -> None:
    result, case = _make_result("my-case-99", finding_titles=[], known_issues=[])
    scorer = BenchmarkScorer()
    score = scorer.score_case(result, case)
    assert score.case_id == "my-case-99"


# ---------------------------------------------------------------------------
# ScoredReport
# ---------------------------------------------------------------------------


def test_scored_report_macro_precision() -> None:
    scores = [
        CaseScore(case_id="a", precision=1.0, recall=0.5, f1=0.67, tp=1, fp=0, fn=1),
        CaseScore(case_id="b", precision=0.5, recall=1.0, f1=0.67, tp=1, fp=1, fn=0),
    ]
    report = ScoredReport(scores=scores)
    assert report.macro_precision == pytest.approx(0.75)


def test_scored_report_macro_recall() -> None:
    scores = [
        CaseScore(case_id="a", precision=1.0, recall=0.5, f1=0.67, tp=1, fp=0, fn=1),
        CaseScore(case_id="b", precision=0.5, recall=1.0, f1=0.67, tp=1, fp=1, fn=0),
    ]
    report = ScoredReport(scores=scores)
    assert report.macro_recall == pytest.approx(0.75)


def test_scored_report_macro_f1() -> None:
    scores = [
        CaseScore(case_id="a", precision=1.0, recall=1.0, f1=1.0, tp=1, fp=0, fn=0),
        CaseScore(case_id="b", precision=0.0, recall=0.0, f1=0.0, tp=0, fp=1, fn=1),
    ]
    report = ScoredReport(scores=scores)
    assert report.macro_f1 == pytest.approx(0.5)


def test_scored_report_empty() -> None:
    report = ScoredReport(scores=[])
    assert report.macro_precision == pytest.approx(0.0)
    assert report.macro_recall == pytest.approx(0.0)
    assert report.macro_f1 == pytest.approx(0.0)


def test_scored_report_to_dict_keys() -> None:
    report = ScoredReport(scores=[])
    d = report.to_dict()
    assert "macro_precision" in d
    assert "macro_recall" in d
    assert "macro_f1" in d
    assert "cases" in d


def test_scored_report_to_dict_cases() -> None:
    scores = [CaseScore(case_id="a", precision=1.0, recall=1.0, f1=1.0, tp=1, fp=0, fn=0)]
    report = ScoredReport(scores=scores)
    d = report.to_dict()
    assert len(d["cases"]) == 1
    assert d["cases"][0]["case_id"] == "a"


# ---------------------------------------------------------------------------
# BenchmarkScorer.score()
# ---------------------------------------------------------------------------


def test_scorer_score_returns_scored_report() -> None:
    from benchmarks.schema import BenchmarkReport

    r1, c1 = _make_result("c1", ["SQL injection"], ["sql injection"])
    r2, c2 = _make_result("c2", [], [])
    report = BenchmarkReport(results=[r1, r2])
    scorer = BenchmarkScorer()
    scored = scorer.score(report, cases=[c1, c2])
    assert isinstance(scored, ScoredReport)
    assert len(scored.scores) == 2


def test_scorer_score_all_perfect() -> None:
    from benchmarks.schema import BenchmarkReport

    r, c = _make_result("c1", ["sql injection found"], ["sql injection"])
    report = BenchmarkReport(results=[r])
    scorer = BenchmarkScorer()
    scored = scorer.score(report, cases=[c])
    assert scored.macro_precision == pytest.approx(1.0)
    assert scored.macro_recall == pytest.approx(1.0)
    assert scored.macro_f1 == pytest.approx(1.0)


def test_scorer_score_empty_report() -> None:
    from benchmarks.schema import BenchmarkReport

    report = BenchmarkReport(results=[])
    scorer = BenchmarkScorer()
    scored = scorer.score(report, cases=[])
    assert scored.macro_f1 == pytest.approx(0.0)
