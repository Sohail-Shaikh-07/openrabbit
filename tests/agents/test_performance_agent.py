"""Tests for PerformanceAgent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.models import ReviewState, Severity
from agents.performance import PerformanceAgent


def _make_state(
    diff: str = "diff --git a/service.py\n+for item in items:\n+    db.query(item)",
    performance_context: list[object] | None = None,
) -> ReviewState:
    pr = MagicMock()
    pr.diff = diff
    retrieval = MagicMock()
    retrieval.performance = performance_context or []
    return {
        "pr_payload": pr,
        "retrieval_result": retrieval,
        "agent_results": [],
        "error": None,
    }


def _llm_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings})


def test_performance_agent_name() -> None:
    assert PerformanceAgent.name == "performance"


@pytest.mark.asyncio
async def test_performance_agent_returns_findings() -> None:
    agent = PerformanceAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "high",
                "file": "service.py",
                "line": 15,
                "confidence": 0.88,
                "title": "N+1 query in loop",
                "reason": "DB query inside loop causes N+1 problem.",
                "suggestion": "Batch the query before the loop.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].category == "performance"
    assert result.findings[0].severity == Severity.high


@pytest.mark.asyncio
async def test_performance_agent_filters_low_confidence() -> None:
    agent = PerformanceAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "medium",
                "file": "utils.py",
                "line": 3,
                "confidence": 0.55,
                "title": "Repeated computation",
                "reason": "Minor repeated calc.",
                "suggestion": "Cache the result.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_performance_agent_handles_malformed_json() -> None:
    agent = PerformanceAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value="oops not json")
        result = await agent.run(state)

    assert result.findings == []
    assert result.agent == "performance"


@pytest.mark.asyncio
async def test_performance_agent_handles_llm_error() -> None:
    agent = PerformanceAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=ConnectionError("no ollama"))
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_performance_agent_includes_project_context_and_impact_guardrails() -> None:
    agent = PerformanceAgent()
    state = _make_state(
        performance_context=[
            {
                "payload": {
                    "source_path": "src/repository.py",
                    "text": "load_orders already batches related account rows.",
                }
            }
        ]
    )
    captured: list[str] = []

    async def fake_generate(prompt: str) -> str:
        captured.append(prompt)
        return _llm_response([])

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=fake_generate)
        await agent.run(state)

    assert "load_orders already batches related account rows." in captured[0]
    assert "concrete runtime impact" in captured[0]
    assert "Do not invent" in captured[0]


@pytest.mark.asyncio
async def test_performance_agent_result_execution_time() -> None:
    agent = PerformanceAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert result.execution_time >= 0.0
