"""Tests for the latency profiler and per-agent timing (OP-33).

All tests are synchronous or use asyncio.run -- no Ollama, no network.
"""

from __future__ import annotations

import asyncio

import pytest

from agents.models import AgentResult, Finding, Severity
from benchmarks.profiler import LatencyProfiler, StepTiming
from benchmarks.runner import BenchmarkRunner
from benchmarks.schema import BenchmarkCase, BenchmarkResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(title: str = "SQL injection") -> Finding:
    return Finding(
        severity=Severity.high,
        category="security",
        file="auth.py",
        line=10,
        confidence=0.9,
        title=title,
        reason="User input reaches SQL.",
        suggestion="Use parameterised queries.",
    )


class _FakeAgent:
    def __init__(self, name: str, findings: list[Finding] | None = None, delay: float = 0.0):
        self.name = name
        self._findings = findings or []
        self._delay = delay

    async def run(self, state: dict) -> AgentResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        return AgentResult(
            agent=self.name,
            findings=self._findings,
            confidence=0.8,
            execution_time=self._delay,
        )


_SIMPLE_DIFF = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n+bad()\n"

# ---------------------------------------------------------------------------
# StepTiming
# ---------------------------------------------------------------------------


def test_step_timing_fields() -> None:
    t = StepTiming(name="security_agent", duration_ms=123.4)
    assert t.name == "security_agent"
    assert t.duration_ms == pytest.approx(123.4)


def test_step_timing_is_frozen() -> None:
    t = StepTiming(name="x", duration_ms=1.0)
    with pytest.raises((AttributeError, TypeError)):
        t.duration_ms = 99.0  # type: ignore[misc]


def test_step_timing_to_dict() -> None:
    t = StepTiming(name="agent", duration_ms=50.0)
    d = t.to_dict()
    assert d["name"] == "agent"
    assert d["duration_ms"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# LatencyProfiler
# ---------------------------------------------------------------------------


def test_profiler_starts_empty() -> None:
    p = LatencyProfiler()
    assert p.timings == []


def test_profiler_measure_records_timing() -> None:
    p = LatencyProfiler()

    async def _inner() -> str:
        return "done"

    asyncio.run(p.measure("step1", _inner()))
    assert len(p.timings) == 1
    assert p.timings[0].name == "step1"


def test_profiler_measure_returns_result() -> None:
    p = LatencyProfiler()

    async def _inner() -> int:
        return 42

    result = asyncio.run(p.measure("step1", _inner()))
    assert result == 42


def test_profiler_measure_duration_positive() -> None:
    p = LatencyProfiler()

    async def _inner() -> None:
        pass

    asyncio.run(p.measure("step1", _inner()))
    assert p.timings[0].duration_ms >= 0.0


def test_profiler_multiple_steps() -> None:
    p = LatencyProfiler()

    async def _run() -> None:
        await p.measure("a", asyncio.sleep(0))
        await p.measure("b", asyncio.sleep(0))

    asyncio.run(_run())
    assert len(p.timings) == 2
    names = [t.name for t in p.timings]
    assert names == ["a", "b"]


def test_profiler_total_ms() -> None:
    p = LatencyProfiler()

    async def _run() -> None:
        await p.measure("a", asyncio.sleep(0))
        await p.measure("b", asyncio.sleep(0))

    asyncio.run(_run())
    assert p.total_ms >= 0.0
    assert p.total_ms == pytest.approx(sum(t.duration_ms for t in p.timings))


def test_profiler_slowest() -> None:
    p = LatencyProfiler()
    # Manually inject timings to avoid sleep
    from benchmarks.profiler import StepTiming

    p._timings = [
        StepTiming(name="fast", duration_ms=10.0),
        StepTiming(name="slow", duration_ms=500.0),
        StepTiming(name="medium", duration_ms=100.0),
    ]
    assert p.slowest.name == "slow"


def test_profiler_slowest_empty_returns_none() -> None:
    p = LatencyProfiler()
    assert p.slowest is None


def test_profiler_to_dict_keys() -> None:
    p = LatencyProfiler()
    d = p.to_dict()
    assert "total_ms" in d
    assert "steps" in d


def test_profiler_to_dict_steps() -> None:
    p = LatencyProfiler()
    from benchmarks.profiler import StepTiming

    p._timings = [StepTiming(name="x", duration_ms=5.0)]
    d = p.to_dict()
    assert len(d["steps"]) == 1
    assert d["steps"][0]["name"] == "x"


def test_profiler_reset_clears_timings() -> None:
    p = LatencyProfiler()
    from benchmarks.profiler import StepTiming

    p._timings = [StepTiming(name="x", duration_ms=5.0)]
    p.reset()
    assert p.timings == []


# ---------------------------------------------------------------------------
# BenchmarkResult per-agent latency field
# ---------------------------------------------------------------------------


def test_benchmark_result_has_agent_latencies() -> None:
    result = BenchmarkResult(
        case_id="c1",
        findings=[],
        agent_results=[],
        latency_ms=100.0,
        agent_latencies={"security": 80.0, "performance": 20.0},
    )
    assert result.agent_latencies["security"] == pytest.approx(80.0)


def test_benchmark_result_agent_latencies_defaults_empty() -> None:
    result = BenchmarkResult(
        case_id="c1",
        findings=[],
        agent_results=[],
        latency_ms=50.0,
    )
    assert result.agent_latencies == {}


def test_benchmark_result_to_dict_includes_agent_latencies() -> None:
    result = BenchmarkResult(
        case_id="c1",
        findings=[],
        agent_results=[],
        latency_ms=50.0,
        agent_latencies={"sec": 40.0},
    )
    d = result.to_dict()
    assert "agent_latencies" in d
    assert d["agent_latencies"]["sec"] == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# BenchmarkRunner per-agent timing
# ---------------------------------------------------------------------------


def test_runner_records_agent_latency() -> None:
    agent = _FakeAgent(name="security", findings=[_make_finding()])
    runner = BenchmarkRunner(agents=[agent])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert "security" in result.agent_latencies
    assert result.agent_latencies["security"] >= 0.0


def test_runner_records_all_agents_latency() -> None:
    a1 = _FakeAgent(name="security")
    a2 = _FakeAgent(name="performance")
    runner = BenchmarkRunner(agents=[a1, a2])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert "security" in result.agent_latencies
    assert "performance" in result.agent_latencies


def test_runner_failed_agent_excluded_from_latencies() -> None:
    class _BrokenAgent:
        name = "broken"

        async def run(self, state: dict) -> AgentResult:
            raise RuntimeError("boom")

    good = _FakeAgent(name="good")
    runner = BenchmarkRunner(agents=[_BrokenAgent(), good])
    case = BenchmarkCase(case_id="c1", diff=_SIMPLE_DIFF, known_issues=[])
    result = asyncio.run(runner.run_case(case))
    assert "good" in result.agent_latencies
    assert "broken" not in result.agent_latencies
