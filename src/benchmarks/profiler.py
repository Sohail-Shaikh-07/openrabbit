"""Latency profiler for the OpenRabbit benchmark harness.

:class:`LatencyProfiler` wraps any async coroutine and records wall-clock
duration per named step. Use it to identify which review agent is the
bottleneck across benchmark runs.
"""

from __future__ import annotations

import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# StepTiming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepTiming:
    """Wall-clock duration for a single named step.

    Attributes
    ----------
    name:
        Identifier for the step (e.g. the agent name).
    duration_ms:
        Elapsed wall-clock time in milliseconds.
    """

    name: str
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "duration_ms": round(self.duration_ms, 3)}


# ---------------------------------------------------------------------------
# LatencyProfiler
# ---------------------------------------------------------------------------


class LatencyProfiler:
    """Collects per-step latency measurements for an async pipeline.

    Usage::

        profiler = LatencyProfiler()
        result = await profiler.measure("security_agent", agent.run(state))
        print(profiler.timings)        # [StepTiming(name="security_agent", ...)]
        print(profiler.total_ms)       # sum of all step durations
        print(profiler.slowest)        # StepTiming with highest duration_ms
    """

    def __init__(self) -> None:
        self._timings: list[StepTiming] = []

    @property
    def timings(self) -> list[StepTiming]:
        return list(self._timings)

    @property
    def total_ms(self) -> float:
        return sum(t.duration_ms for t in self._timings)

    @property
    def slowest(self) -> StepTiming | None:
        if not self._timings:
            return None
        return max(self._timings, key=lambda t: t.duration_ms)

    async def measure(self, name: str, coro: Coroutine[Any, Any, T]) -> T:
        """Await *coro*, record its wall-clock duration under *name*, return its result."""
        started = time.monotonic()
        result = await coro
        elapsed_ms = (time.monotonic() - started) * 1000
        self._timings.append(StepTiming(name=name, duration_ms=elapsed_ms))
        return result

    def reset(self) -> None:
        """Clear all recorded timings."""
        self._timings = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_ms": round(self.total_ms, 3),
            "steps": [t.to_dict() for t in self._timings],
        }
