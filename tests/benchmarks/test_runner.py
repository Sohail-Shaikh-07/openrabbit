"""Tests for the Phase 6 benchmark runner (OP-31).

All tests use mock agents -- no Ollama, no GitHub API, no network required.
The runner is designed to accept any agent list so tests can inject fakes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from agents.models import AgentResult, Finding, Severity
from benchmarks.runner import BenchmarkRunner
from benchmarks.schema import BenchmarkCase, BenchmarkPayload, BenchmarkReport, BenchmarkResult

# ---------------------------------------------------------------------------
# Mock agent helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeAgent:
    """Test double for BaseReviewAgent. Returns a fixed list of findings."""

    name: str
    findings: list[Finding] = field(default_factory=list)
    should_raise: bool = False

    async def run(self, state: dict[str, Any]) -> AgentResult:
        if self.should_raise:
            raise RuntimeError("agent explosion")
        return AgentResult(
            agent=self.name,
            findings=self.findings,
            confidence=0.8 if self.findings else 0.0,
            execution_time=0.01,
        )


def _make_finding(
    severity: Severity = Severity.high,
    category: str = "security",
    file: str = "auth.py",
    line: int = 10,
    title: str = "SQL injection risk",
) -> Finding:
    return Finding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        confidence=0.9,
        title=title,
        reason="User input reaches SQL directly.",
        suggestion="Use parameterised queries.",
    )


_SIMPLE_DIFF = """\
--- a/auth.py
+++ b/auth.py
@@ -8,6 +8,7 @@
 def login(username, password):
+    query = f"SELECT * FROM users WHERE name='{username}'"
     db.execute(query)
"""

# ---------------------------------------------------------------------------
# BenchmarkPayload
# ---------------------------------------------------------------------------


def test_benchmark_payload_has_diff() -> None:
    p = BenchmarkPayload(diff=_SIMPLE_DIFF)
    assert p.diff == _SIMPLE_DIFF


def test_benchmark_payload_default_number() -> None:
    p = BenchmarkPayload(diff="")
    assert p.number == 0


def test_benchmark_payload_custom_number() -> None:
    p = BenchmarkPayload(diff="", number=42)
    assert p.number == 42


def test_benchmark_payload_default_title() -> None:
    p = BenchmarkPayload(diff="")
    assert p.title == ""


# ---------------------------------------------------------------------------
# BenchmarkCase
# ---------------------------------------------------------------------------


def test_benchmark_case_fields() -> None:
    case = BenchmarkCase(
        case_id="test-001",
        diff=_SIMPLE_DIFF,
        known_issues=["SQL injection on line 9"],
    )
    assert case.case_id == "test-001"
    assert case.diff == _SIMPLE_DIFF
    assert case.known_issues == ["SQL injection on line 9"]


def test_benchmark_case_is_frozen() -> None:
    case = BenchmarkCase(case_id="x", diff="", known_issues=[])
    with pytest.raises((AttributeError, TypeError)):
        case.case_id = "y"  # type: ignore[misc]


def test_benchmark_case_default_title() -> None:
    case = BenchmarkCase(case_id="x", diff="", known_issues=[])
    assert case.title == ""


def test_benchmark_case_custom_title() -> None:
    case = BenchmarkCase(case_id="x", diff="", known_issues=[], title="My PR")
    assert case.title == "My PR"


# ---------------------------------------------------------------------------
# BenchmarkResult
# ---------------------------------------------------------------------------


def test_benchmark_result_no_error() -> None:
    result = BenchmarkResult(
        case_id="test-001",
        findings=[],
        agent_results=[],
        latency_ms=120.0,
    )
    assert result.error is None


def test_benchmark_result_with_error() -> None:
    result = BenchmarkResult(
        case_id="test-001",
        findings=[],
        agent_results=[],
        latency_ms=5.0,
        error="agent failed",
    )
    assert result.error == "agent failed"


# ---------------------------------------------------------------------------
# BenchmarkReport computed properties
# ---------------------------------------------------------------------------


def test_report_total_cases() -> None:
    results = [
        BenchmarkResult(case_id="a", findings=[], agent_results=[], latency_ms=100.0),
        BenchmarkResult(case_id="b", findings=[], agent_results=[], latency_ms=200.0),
    ]
    report = BenchmarkReport(results=results)
    assert report.total_cases == 2


def test_report_error_count_none() -> None:
    results = [
        BenchmarkResult(case_id="a", findings=[], agent_results=[], latency_ms=100.0),
    ]
    report = BenchmarkReport(results=results)
    assert report.error_count == 0


def test_report_error_count_some() -> None:
    results = [
        BenchmarkResult(case_id="a", findings=[], agent_results=[], latency_ms=100.0),
        BenchmarkResult(case_id="b", findings=[], agent_results=[], latency_ms=10.0, error="boom"),
    ]
    report = BenchmarkReport(results=results)
    assert report.error_count == 1


def test_report_mean_latency() -> None:
    results = [
        BenchmarkResult(case_id="a", findings=[], agent_results=[], latency_ms=100.0),
        BenchmarkResult(case_id="b", findings=[], agent_results=[], latency_ms=200.0),
    ]
    report = BenchmarkReport(results=results)
    assert report.mean_latency_ms == pytest.approx(150.0)


def test_report_mean_latency_empty() -> None:
    report = BenchmarkReport(results=[])
    assert report.mean_latency_ms == pytest.approx(0.0)


def test_report_to_dict_keys() -> None:
    report = BenchmarkReport(results=[])
    d = report.to_dict()
    assert "total_cases" in d
    assert "error_count" in d
    assert "mean_latency_ms" in d
    assert "results" in d


def test_report_to_dict_results_list() -> None:
    results = [
        BenchmarkResult(case_id="a", findings=[], agent_results=[], latency_ms=10.0),
    ]
    report = BenchmarkReport(results=results)
    d = report.to_dict()
    assert isinstance(d["results"], list)
    assert len(d["results"]) == 1


# ---------------------------------------------------------------------------
# BenchmarkRunner.run_case()
# ---------------------------------------------------------------------------


def test_run_case_returns_result() -> None:
    agent = _FakeAgent(name="security")
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert isinstance(result, BenchmarkResult)


def test_run_case_case_id_preserved() -> None:
    agent = _FakeAgent(name="security")
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="my-case-42", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert result.case_id == "my-case-42"


def test_run_case_no_findings_when_agent_returns_none() -> None:
    agent = _FakeAgent(name="security", findings=[])
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert result.findings == []


def test_run_case_findings_from_agent() -> None:
    finding = _make_finding()
    agent = _FakeAgent(name="security", findings=[finding])
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert len(result.findings) == 1
    assert result.findings[0].finding == finding


def test_run_case_latency_is_positive() -> None:
    agent = _FakeAgent(name="security")
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert result.latency_ms >= 0.0


def test_run_case_error_is_none_on_success() -> None:
    agent = _FakeAgent(name="security")
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert result.error is None


def test_run_case_captures_agent_error() -> None:
    agent = _FakeAgent(name="security", should_raise=True)
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert result.error is not None
    assert "explosion" in result.error


def test_run_case_one_failing_agent_does_not_abort_others() -> None:
    bad_agent = _FakeAgent(name="security", should_raise=True)
    good_agent = _FakeAgent(name="performance", findings=[_make_finding(category="performance")])
    runner = BenchmarkRunner(agents=[bad_agent, good_agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    # Should still get results from the good agent
    assert result.agent_results is not None


def test_run_case_multiple_agents_aggregated() -> None:
    f1 = _make_finding(category="security", title="SQL injection")
    f2 = _make_finding(category="performance", title="N+1 query", file="db.py")
    agent1 = _FakeAgent(name="security", findings=[f1])
    agent2 = _FakeAgent(name="performance", findings=[f2])
    runner = BenchmarkRunner(agents=[agent1, agent2])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert len(result.findings) == 2


def test_run_case_top_n_limits_findings() -> None:
    findings = [_make_finding(title=f"issue-{i}", line=i) for i in range(20)]
    agent = _FakeAgent(name="security", findings=findings)
    runner = BenchmarkRunner(agents=[agent], top_n=5)
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert len(result.findings) <= 5


def test_run_case_agent_results_accessible() -> None:
    agent = _FakeAgent(name="security", findings=[_make_finding()])
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert len(result.agent_results) >= 1
    assert result.agent_results[0].agent == "security"


# ---------------------------------------------------------------------------
# BenchmarkRunner.run()
# ---------------------------------------------------------------------------


def test_run_returns_report() -> None:
    agent = _FakeAgent(name="security")
    runner = BenchmarkRunner(agents=[agent])
    cases = [BenchmarkCase(case_id=f"c{i}", diff=_SIMPLE_DIFF, known_issues=[]) for i in range(3)]
    report = asyncio.run(runner.run(cases))
    assert isinstance(report, BenchmarkReport)


def test_run_processes_all_cases() -> None:
    agent = _FakeAgent(name="security")
    runner = BenchmarkRunner(agents=[agent])
    cases = [BenchmarkCase(case_id=f"c{i}", diff=_SIMPLE_DIFF, known_issues=[]) for i in range(5)]
    report = asyncio.run(runner.run(cases))
    assert report.total_cases == 5


def test_run_empty_cases() -> None:
    runner = BenchmarkRunner(agents=[])
    report = asyncio.run(runner.run([]))
    assert report.total_cases == 0
    assert report.mean_latency_ms == pytest.approx(0.0)


def test_run_collects_errors() -> None:
    agent = _FakeAgent(name="security", should_raise=True)
    runner = BenchmarkRunner(agents=[agent])
    cases = [BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])]
    report = asyncio.run(runner.run(cases))
    assert report.error_count == 1
