"""Data schemas for the OpenRabbit benchmark harness.

Three levels of result data:

:class:`BenchmarkCase`
    One benchmark input: a raw diff string and the list of known issues that
    a correct reviewer should catch. No GitHub API connection required.

:class:`BenchmarkResult`
    The output for a single case: ranked findings, raw agent results, timing,
    and an optional error string when the run failed.

:class:`BenchmarkReport`
    Aggregated results for an entire benchmark run. Provides computed
    properties (total_cases, error_count, mean_latency_ms) and a
    ``to_dict()`` serialiser for JSON output.

:class:`BenchmarkPayload`
    A lightweight substitute for :class:`~github_.pr.PullRequestPayload` that
    review agents can consume without a GitHub API connection. Exposes the
    same ``.diff`` attribute that agents read via ``getattr(payload, "diff", "")``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# BenchmarkPayload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkPayload:
    """Minimal payload object accepted by review agents in benchmark mode.

    Agents read PR content via ``getattr(state["pr_payload"], "diff", "")``.
    This class satisfies that contract without requiring GitHub API access.

    Attributes
    ----------
    diff:
        Full unified diff string for the benchmark case.
    number:
        Synthetic PR number (defaults to 0 for benchmark cases).
    title:
        Human-readable title for the benchmark case.
    files:
        Empty list -- agents that iterate files skip gracefully.
    commits:
        Empty list -- agents that inspect commits skip gracefully.
    """

    diff: str
    number: int = 0
    title: str = ""
    files: list[Any] = field(default_factory=list)
    commits: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# BenchmarkCase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkCase:
    """One benchmark evaluation case.

    Attributes
    ----------
    case_id:
        Unique identifier for this case (e.g. ``"sweprbench-001"``).
    diff:
        Unified diff string representing the pull request changes.
    known_issues:
        Ground-truth list of issue descriptions that a correct reviewer
        should identify. Used by the scoring layer (OP-32).
    title:
        Optional human-readable title for this benchmark case.
    """

    case_id: str
    diff: str
    known_issues: list[str]
    title: str = ""


# ---------------------------------------------------------------------------
# BenchmarkResult
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    """Review output for a single benchmark case.

    Attributes
    ----------
    case_id:
        Identifier matching the input :class:`BenchmarkCase`.
    findings:
        Ranked findings produced by the review agents.
    agent_results:
        Raw per-agent outputs before ranking.
    latency_ms:
        Wall-clock time for the full review pipeline, in milliseconds.
    error:
        Set to an error description when the run failed; ``None`` on success.
    """

    case_id: str
    findings: list[Any]
    agent_results: list[Any]
    latency_ms: float
    error: str | None = None
    agent_latencies: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "agent_latencies": {k: round(v, 3) for k, v in self.agent_latencies.items()},
            "findings_count": len(self.findings),
            "findings": [
                {
                    **rf.finding.as_dict(),
                    "score": round(rf.score, 4),
                }
                for rf in self.findings
            ],
        }


# ---------------------------------------------------------------------------
# BenchmarkReport
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkReport:
    """Aggregated results from a full benchmark run.

    Attributes
    ----------
    results:
        One :class:`BenchmarkResult` per :class:`BenchmarkCase` in the run.
    """

    results: list[BenchmarkResult]

    @property
    def total_cases(self) -> int:
        return len(self.results)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def mean_latency_ms(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.latency_ms for r in self.results) / len(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "error_count": self.error_count,
            "mean_latency_ms": round(self.mean_latency_ms, 2),
            "results": [r.to_dict() for r in self.results],
        }
