"""Tests for TestCoverageAgent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.models import ReviewState, Severity
from agents.test_coverage import TestCoverageAgent


def _make_state(
    diff: str = "diff --git a/src/auth.py\n+def validate_token(token: str) -> bool:\n+    return len(token) > 0",
    test_context: list[str] | None = None,
) -> ReviewState:
    pr = MagicMock()
    pr.diff = diff
    retrieval = MagicMock()
    retrieval.tests = test_context or ["tests/test_auth.py: test_login, test_logout"]
    return {
        "pr_payload": pr,
        "retrieval_result": retrieval,
        "agent_results": [],
        "error": None,
    }


def _llm_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings})


def test_test_coverage_agent_name() -> None:
    assert TestCoverageAgent.name == "test_coverage"


@pytest.mark.asyncio
async def test_test_coverage_agent_returns_findings() -> None:
    agent = TestCoverageAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "medium",
                "file": "src/auth.py",
                "line": 1,
                "confidence": 0.82,
                "title": "validate_token has no test",
                "reason": "New function added without a corresponding unit test.",
                "suggestion": "Add test_validate_token to tests/test_auth.py.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].category == "tests"
    assert result.findings[0].severity == Severity.medium


@pytest.mark.asyncio
async def test_test_coverage_agent_includes_test_context_in_prompt() -> None:
    agent = TestCoverageAgent()
    test_context = ["tests/test_payments.py: test_charge, test_refund"]
    state = _make_state(test_context=test_context)

    captured: list[str] = []

    async def fake_generate(prompt: str) -> str:
        captured.append(prompt)
        return _llm_response([])

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=fake_generate)
        await agent.run(state)

    assert "tests/test_payments.py" in captured[0]
    assert "Do not invent" in captured[0]
    assert "changed lines" in captured[0]


@pytest.mark.asyncio
async def test_test_coverage_agent_filters_low_confidence() -> None:
    agent = TestCoverageAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "low",
                "file": "src/utils.py",
                "line": 10,
                "confidence": 0.40,
                "title": "Might need test",
                "reason": "Uncertain.",
                "suggestion": "Consider adding a test.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_test_coverage_agent_handles_malformed_json() -> None:
    agent = TestCoverageAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value="bad json")
        result = await agent.run(state)

    assert result.findings == []
    assert result.agent == "test_coverage"


@pytest.mark.asyncio
async def test_test_coverage_agent_handles_llm_error() -> None:
    agent = TestCoverageAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=RuntimeError("timeout"))
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_test_coverage_agent_works_without_retrieval() -> None:
    agent = TestCoverageAgent()
    pr = MagicMock()
    pr.diff = "diff"
    state: ReviewState = {
        "pr_payload": pr,
        "retrieval_result": None,
        "agent_results": [],
        "error": None,
    }

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert result.agent == "test_coverage"
