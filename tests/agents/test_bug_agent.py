"""Tests for BugDetectionAgent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.bugs import BugDetectionAgent
from agents.models import ReviewState, Severity


def _make_state(
    diff: str = "diff --git a/app.py\n+result = user.profile.name",
    bug_context: list[object] | None = None,
) -> ReviewState:
    pr = MagicMock()
    pr.diff = diff
    retrieval = MagicMock()
    retrieval.bug = bug_context or []
    retrieval.architecture = bug_context or []
    retrieval.security = bug_context or []
    return {
        "pr_payload": pr,
        "retrieval_result": retrieval,
        "agent_results": [],
        "error": None,
    }


def _llm_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings})


def test_bug_detection_agent_name() -> None:
    assert BugDetectionAgent.name == "bug_detection"


@pytest.mark.asyncio
async def test_bug_agent_returns_findings() -> None:
    agent = BugDetectionAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "high",
                "file": "app.py",
                "line": 12,
                "confidence": 0.85,
                "title": "Potential null dereference",
                "reason": "user.profile may be None when user is not logged in.",
                "suggestion": "Check user.profile is not None before accessing name.",
                "fix": "if user.profile: return user.profile.name",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].category == "bug"
    assert result.findings[0].severity == Severity.high
    assert result.findings[0].title == "Potential null dereference"


@pytest.mark.asyncio
async def test_bug_agent_filters_low_confidence() -> None:
    agent = BugDetectionAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "medium",
                "file": "utils.py",
                "line": 5,
                "confidence": 0.60,
                "title": "Possible off-by-one",
                "reason": "Index might exceed bounds.",
                "suggestion": "Use <= instead of <.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_bug_agent_handles_malformed_json() -> None:
    agent = BugDetectionAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value="{{broken")
        result = await agent.run(state)

    assert result.findings == []
    assert result.agent == "bug_detection"


@pytest.mark.asyncio
async def test_bug_agent_handles_llm_error() -> None:
    agent = BugDetectionAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=TimeoutError("ollama timeout"))
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_bug_agent_multiple_findings() -> None:
    agent = BugDetectionAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "critical",
                "file": "worker.py",
                "line": 30,
                "confidence": 0.92,
                "title": "Race condition on shared state",
                "reason": "Shared counter modified without lock.",
                "suggestion": "Use threading.Lock.",
                "fix": "",
            },
            {
                "severity": "medium",
                "file": "parser.py",
                "line": 8,
                "confidence": 0.75,
                "title": "Unhandled exception",
                "reason": "ValueError not caught.",
                "suggestion": "Wrap in try/except.",
                "fix": "",
            },
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert len(result.findings) == 2
    severities = {f.title: f.severity for f in result.findings}
    assert severities["Race condition on shared state"] == Severity.critical
    assert severities["Unhandled exception"] == Severity.medium


@pytest.mark.asyncio
async def test_bug_agent_includes_project_context_and_changed_line_guardrails() -> None:
    agent = BugDetectionAgent()
    state = _make_state(
        bug_context=[
            {
                "payload": {
                    "source_path": ".openrabbit/coding_rules.md",
                    "text": "All public handlers must validate empty input explicitly.",
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

    assert "All public handlers must validate empty input explicitly." in captured[0]
    assert "changed lines" in captured[0]
    assert "Do not invent" in captured[0]


@pytest.mark.asyncio
async def test_bug_agent_result_has_correct_agent_name() -> None:
    agent = BugDetectionAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert result.agent == "bug_detection"
    assert result.execution_time >= 0.0
