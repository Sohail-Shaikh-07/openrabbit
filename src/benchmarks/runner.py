"""Benchmark runner for the OpenRabbit evaluation harness.

:class:`BenchmarkRunner` drives each :class:`~benchmarks.schema.BenchmarkCase`
through a list of review agents, collects the raw :class:`~agents.models.AgentResult`
objects, ranks the findings with :class:`~ranking.ranker.CommentRanker`, and
returns a :class:`~benchmarks.schema.BenchmarkResult`.

Design decisions:

- Agents are injected at construction time so unit tests can pass mock agents
  without instantiating Ollama-dependent production code.
- Agents are called sequentially for benchmark determinism. One failing agent
  logs a warning but does not abort the remaining agents for the same case.
- The runner builds a :class:`~benchmarks.schema.BenchmarkPayload` from each
  case's diff string and passes it as ``state["pr_payload"]``. Review agents
  read the diff via ``getattr(payload, "diff", "")``, so no changes to the
  agent contract are required.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.models import AgentResult
from benchmarks.schema import BenchmarkCase, BenchmarkPayload, BenchmarkReport, BenchmarkResult
from ranking.ranker import CommentRanker

logger = logging.getLogger(__name__)

_DEFAULT_TOP_N = 10


class BenchmarkRunner:
    """Runs benchmark cases through a set of review agents.

    Parameters
    ----------
    agents:
        Review agents to run on every benchmark case. Each must implement
        an async ``run(state) -> AgentResult`` method.
    top_n:
        Maximum number of ranked findings to return per case.
    """

    def __init__(self, agents: list[Any], top_n: int = _DEFAULT_TOP_N) -> None:
        self._agents = agents
        self._ranker = CommentRanker(top_n=top_n)

    async def run_case(self, case: BenchmarkCase) -> BenchmarkResult:
        """Run a single benchmark case through all agents.

        Parameters
        ----------
        case:
            The benchmark case to evaluate.

        Returns
        -------
        BenchmarkResult
            Always returns a result. If all agents fail, ``error`` is set to
            the last exception message and ``findings`` is empty.
        """
        started = time.monotonic()
        payload = BenchmarkPayload(diff=case.diff, title=case.title)
        state: dict[str, Any] = {"pr_payload": payload, "agent_results": []}

        agent_results: list[AgentResult] = []
        last_error: str | None = None

        for agent in self._agents:
            try:
                result = await agent.run(state)
                agent_results.append(result)
            except Exception as exc:
                logger.warning("Agent %s failed on case %s: %s", agent.name, case.case_id, exc)
                last_error = str(exc)

        elapsed_ms = (time.monotonic() - started) * 1000

        if not agent_results and self._agents:
            return BenchmarkResult(
                case_id=case.case_id,
                findings=[],
                agent_results=[],
                latency_ms=elapsed_ms,
                error=last_error,
            )

        findings = self._ranker.rank(agent_results)
        return BenchmarkResult(
            case_id=case.case_id,
            findings=findings,
            agent_results=agent_results,
            latency_ms=elapsed_ms,
        )

    async def run(self, cases: list[BenchmarkCase]) -> BenchmarkReport:
        """Run all benchmark cases sequentially.

        Parameters
        ----------
        cases:
            Cases to evaluate. Empty list produces an empty report.

        Returns
        -------
        BenchmarkReport
            Aggregated results for the entire run.
        """
        results: list[BenchmarkResult] = []
        for i, case in enumerate(cases, start=1):
            logger.info("Running benchmark case %d/%d: %s", i, len(cases), case.case_id)
            result = await self.run_case(case)
            results.append(result)
        return BenchmarkReport(results=results)
