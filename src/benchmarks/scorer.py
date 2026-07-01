"""Precision/recall scoring for the OpenRabbit benchmark harness.

:class:`BenchmarkScorer` compares the ranked findings produced by
:class:`~benchmarks.runner.BenchmarkRunner` against the ground-truth
``known_issues`` list in each :class:`~benchmarks.schema.BenchmarkCase` and
computes per-case and macro-averaged precision, recall, and F1.

Matching strategy
-----------------
A finding is counted as a **true positive** for a known issue if the issue
keyword appears (case-insensitive substring) in the finding's ``title`` **or**
``reason`` field. Each known issue can match at most one finding; each finding
can match at most one known issue (greedy left-to-right assignment).

This keeps the scorer fast and dependency-free. Semantic/embedding-based
matching can replace the substring check in a later iteration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchmarks.schema import BenchmarkCase, BenchmarkReport, BenchmarkResult


# ---------------------------------------------------------------------------
# CaseScore
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseScore:
    """Precision/recall/F1 scores for a single benchmark case.

    Attributes
    ----------
    case_id:
        Identifier matching the evaluated :class:`~benchmarks.schema.BenchmarkCase`.
    precision:
        Fraction of returned findings that are true positives.
    recall:
        Fraction of known issues that were found.
    f1:
        Harmonic mean of precision and recall.
    tp:
        Number of true positives.
    fp:
        Number of false positives (findings not in known_issues).
    fn:
        Number of false negatives (known issues not found).
    """

    case_id: str
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
        }


# ---------------------------------------------------------------------------
# ScoredReport
# ---------------------------------------------------------------------------


@dataclass
class ScoredReport:
    """Macro-averaged scores across all benchmark cases.

    Attributes
    ----------
    scores:
        Per-case :class:`CaseScore` objects.
    """

    scores: list[CaseScore]

    @property
    def macro_precision(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.precision for s in self.scores) / len(self.scores)

    @property
    def macro_recall(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.recall for s in self.scores) / len(self.scores)

    @property
    def macro_f1(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.f1 for s in self.scores) / len(self.scores)

    def to_dict(self) -> dict[str, Any]:
        return {
            "macro_precision": round(self.macro_precision, 4),
            "macro_recall": round(self.macro_recall, 4),
            "macro_f1": round(self.macro_f1, 4),
            "cases": [s.to_dict() for s in self.scores],
        }


# ---------------------------------------------------------------------------
# BenchmarkScorer
# ---------------------------------------------------------------------------


def _matches(finding: Any, keyword: str) -> bool:
    """Return True if ``keyword`` appears in the finding's title or reason."""
    needle = keyword.lower()
    title: str = getattr(finding, "title", "") or ""
    reason: str = getattr(finding, "reason", "") or ""
    return needle in title.lower() or needle in reason.lower()


class BenchmarkScorer:
    """Scores a benchmark run by comparing findings against known issues.

    The scorer uses a greedy substring-match strategy:

    1. For each ranked finding, check whether its title or reason contains any
       unmatched known-issue keyword (case-insensitive).
    2. The first matching issue claims the finding as a TP; subsequent findings
       cannot re-claim the same issue.
    3. Findings with no matching issue are FPs; issues with no matching finding
       are FNs.

    Edge cases
    ----------
    - No findings **and** no known issues: precision=recall=f1=1.0 (perfect
      silence on a clean diff).
    - No findings **but** known issues exist: recall=0 as expected.
    - Findings present **but** no known issues: precision=0; recall is defined
      as 1.0 (nothing to find, nothing missed).
    """

    def score_case(
        self,
        result: BenchmarkResult,
        case: BenchmarkCase,
    ) -> CaseScore:
        """Score a single benchmark case.

        Parameters
        ----------
        result:
            The :class:`~benchmarks.schema.BenchmarkResult` produced by
            :class:`~benchmarks.runner.BenchmarkRunner`.
        case:
            The ground-truth :class:`~benchmarks.schema.BenchmarkCase`
            supplying ``known_issues``.

        Returns
        -------
        CaseScore
        """
        known = list(case.known_issues)
        findings = list(result.findings)

        if not known and not findings:
            return CaseScore(
                case_id=case.case_id,
                precision=1.0,
                recall=1.0,
                f1=1.0,
                tp=0,
                fp=0,
                fn=0,
            )

        matched_issues: set[int] = set()
        tp = 0
        fp = 0

        for ranked in findings:
            f = ranked.finding
            matched = False
            for idx, keyword in enumerate(known):
                if idx in matched_issues:
                    continue
                if _matches(f, keyword):
                    matched_issues.add(idx)
                    tp += 1
                    matched = True
                    break
            if not matched:
                fp += 1

        fn = len(known) - len(matched_issues)

        # Precision: tp / (tp + fp). If no findings, 0/0 -> 0.
        # Special case: no known issues but findings exist -> precision=0, recall=1.
        if not known:
            precision = 0.0
            recall = 1.0
        else:
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / len(known) if known else 1.0

        f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0

        return CaseScore(
            case_id=case.case_id,
            precision=precision,
            recall=recall,
            f1=f1,
            tp=tp,
            fp=fp,
            fn=fn,
        )

    def score(
        self,
        report: BenchmarkReport,
        cases: list[BenchmarkCase],
    ) -> ScoredReport:
        """Score all cases in a benchmark report.

        Parameters
        ----------
        report:
            The :class:`~benchmarks.schema.BenchmarkReport` from a full run.
        cases:
            The original :class:`~benchmarks.schema.BenchmarkCase` list in the
            same order as ``report.results``.

        Returns
        -------
        ScoredReport
        """
        scores: list[CaseScore] = []
        case_by_id = {c.case_id: c for c in cases}

        for result in report.results:
            case = case_by_id.get(result.case_id)
            if case is None:
                continue
            scores.append(self.score_case(result, case))

        return ScoredReport(scores=scores)
