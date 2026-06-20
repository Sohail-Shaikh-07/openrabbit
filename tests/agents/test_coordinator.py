"""Tests for the LangGraph coordinator and base agent types."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.coordinator import CoordinatorGraph
from agents.models import (
    AgentResult,
    Finding,
    ReviewState,
    Severity,
)

# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


def test_finding_fields_exist() -> None:
    f = Finding(
        severity=Severity.high,
        category="security",
        file="auth.py",
        line=42,
        confidence=0.92,
        title="SQL Injection Risk",
        reason="Unsanitized input used in query.",
        suggestion="Use parameterized queries.",
        fix="cursor.execute('SELECT * FROM t WHERE id=?', (user_id,))",
    )
    assert f.severity is Severity.high
    assert f.file == "auth.py"
    assert f.confidence == pytest.approx(0.92)


def test_severity_ordering() -> None:
    levels = [Severity.low, Severity.medium, Severity.high, Severity.critical]
    assert levels == sorted(levels)


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------


def test_agent_result_stores_findings() -> None:
    finding = Finding(
        severity=Severity.medium,
        category="bug",
        file="app.py",
        line=10,
        confidence=0.75,
        title="Null dereference",
        reason="user may be None",
        suggestion="Add a None check",
        fix="if user is None: return",
    )
    result = AgentResult(
        agent="bug_detection",
        findings=[finding],
        confidence=0.80,
        execution_time=1.2,
    )
    assert result.agent == "bug_detection"
    assert len(result.findings) == 1
    assert result.execution_time == pytest.approx(1.2)


def test_agent_result_empty_findings() -> None:
    result = AgentResult(agent="security", findings=[], confidence=0.5, execution_time=0.1)
    assert result.findings == []


# ---------------------------------------------------------------------------
# ReviewState
# ---------------------------------------------------------------------------


def test_review_state_has_required_keys() -> None:
    state: ReviewState = {
        "pr_payload": MagicMock(),
        "retrieval_result": MagicMock(),
        "agent_results": [],
        "error": None,
    }
    assert "pr_payload" in state
    assert "agent_results" in state


# ---------------------------------------------------------------------------
# CoordinatorGraph
# ---------------------------------------------------------------------------


def test_coordinator_graph_builds_without_error() -> None:
    graph = CoordinatorGraph(agents=[])
    compiled = graph.compile()
    assert compiled is not None


def test_coordinator_graph_accepts_agent_list() -> None:
    mock_agent = MagicMock()
    mock_agent.name = "mock_agent"
    graph = CoordinatorGraph(agents=[mock_agent])
    compiled = graph.compile()
    assert compiled is not None


@pytest.mark.asyncio
async def test_coordinator_invokes_registered_agents() -> None:
    finding = Finding(
        severity=Severity.high,
        category="security",
        file="app.py",
        line=5,
        confidence=0.9,
        title="Test finding",
        reason="test",
        suggestion="fix it",
        fix="",
    )

    from agents.base import BaseReviewAgent

    class MockAgent(BaseReviewAgent):
        name = "mock"

        async def run(self, state: ReviewState) -> AgentResult:
            return AgentResult(
                agent=self.name,
                findings=[finding],
                confidence=0.9,
                execution_time=0.1,
            )

    graph = CoordinatorGraph(agents=[MockAgent()])
    compiled = graph.compile()

    pr = MagicMock()
    pr.files = []
    retrieval = MagicMock()
    retrieval.security = []
    retrieval.architecture = []
    retrieval.performance = []
    retrieval.tests = []

    initial_state: ReviewState = {
        "pr_payload": pr,
        "retrieval_result": retrieval,
        "agent_results": [],
        "error": None,
    }
    result = await compiled.ainvoke(initial_state)

    assert "agent_results" in result
    assert len(result["agent_results"]) == 1
    assert result["agent_results"][0].agent == "mock"
    assert len(result["agent_results"][0].findings) == 1


@pytest.mark.asyncio
async def test_coordinator_continues_when_agent_raises() -> None:
    from agents.base import BaseReviewAgent

    class FailingAgent(BaseReviewAgent):
        name = "failing"

        async def run(self, state: ReviewState) -> AgentResult:
            raise RuntimeError("agent error")

    graph = CoordinatorGraph(agents=[FailingAgent()])
    compiled = graph.compile()

    pr = MagicMock()
    pr.files = []
    retrieval = MagicMock()

    initial_state: ReviewState = {
        "pr_payload": pr,
        "retrieval_result": retrieval,
        "agent_results": [],
        "error": None,
    }
    result = await compiled.ainvoke(initial_state)

    # Failing agent should contribute an empty result, not crash the graph.
    assert "agent_results" in result
    assert result["agent_results"][0].findings == []
