"""Phase 6 evaluation benchmark harness for OpenRabbit."""

from __future__ import annotations

from benchmarks.corpus import (
    DEFAULT_V1_1_CORPUS,
    DEFAULT_V1_7_CONTEXT_PRECISION_CORPUS,
    CorpusFormatError,
    load_benchmark_cases,
)
from benchmarks.profiler import LatencyProfiler, StepTiming
from benchmarks.runner import BenchmarkRunner
from benchmarks.schema import BenchmarkCase, BenchmarkPayload, BenchmarkReport, BenchmarkResult
from benchmarks.scorer import BenchmarkScorer, CaseScore, ScoredReport

__all__ = [
    "DEFAULT_V1_1_CORPUS",
    "DEFAULT_V1_7_CONTEXT_PRECISION_CORPUS",
    "BenchmarkCase",
    "BenchmarkPayload",
    "BenchmarkReport",
    "BenchmarkResult",
    "BenchmarkRunner",
    "BenchmarkScorer",
    "CaseScore",
    "CorpusFormatError",
    "LatencyProfiler",
    "ScoredReport",
    "StepTiming",
    "load_benchmark_cases",
]
