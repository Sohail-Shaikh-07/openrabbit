"""Shared data models for the OpenRabbit multi-agent review system.

All agents consume and produce these types. The shapes mirror the agent
contract defined in ``.agent/agent-specefication.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class Severity(IntEnum):
    """Ordered severity levels. Higher integer = more severe.

    The ordering supports ``sorted()`` and comparison operators so findings
    can be ranked by severity without extra mapping tables.
    """

    low = 1
    medium = 2
    high = 3
    critical = 4


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One actionable finding produced by a review agent.

    Attributes
    ----------
    severity:
        How serious this finding is.
    category:
        Which concern raised this finding (``"security"``, ``"performance"``,
        ``"bug"``, ``"architecture"``, ``"tests"``, ``"style"``).
    file:
        Repository-relative path of the affected file.
    line:
        Approximate line number. ``0`` when the finding applies to the whole
        file rather than a specific location.
    confidence:
        Model confidence in the range ``[0, 1]``. Findings below the agent's
        minimum threshold are discarded before storage.
    title:
        One-line summary of the issue.
    reason:
        Why this is a problem.
    suggestion:
        What to do instead.
    fix:
        Optional code snippet showing the corrected code. May be empty.
    """

    severity: Severity
    category: str
    file: str
    line: int
    confidence: float
    title: str
    reason: str
    suggestion: str
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.name,
            "category": self.category,
            "file": self.file,
            "line": self.line,
            "confidence": self.confidence,
            "title": self.title,
            "reason": self.reason,
            "suggestion": self.suggestion,
            "fix": self.fix,
        }


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """The output produced by one review agent.

    Attributes
    ----------
    agent:
        Machine-readable name of the agent that produced this result.
    findings:
        Findings emitted by the agent. May be empty if nothing was found.
    confidence:
        Agent-level confidence (the average or minimum of its findings, or a
        model-level score when no findings were produced).
    execution_time:
        Wall-clock seconds the agent spent running.
    """

    agent: str
    findings: list[Finding]
    confidence: float
    execution_time: float


# ---------------------------------------------------------------------------
# ReviewState
# ---------------------------------------------------------------------------


class ReviewState(TypedDict, total=False):
    """LangGraph shared state object threaded through all nodes.

    ``total=False`` means all keys are optional at the TypedDict level so
    LangGraph can start with a partial state and nodes can add keys as they
    complete.
    """

    pr_payload: Any
    """The :class:`~github_.pr.PullRequestPayload` being reviewed."""

    retrieval_result: Any
    """The :class:`~rag.retriever.RetrievalResult` for this PR."""

    agent_results: list[AgentResult]
    """Accumulated results from all review agents."""

    error: str | None
    """Set to an error message if an unrecoverable failure occurred."""
