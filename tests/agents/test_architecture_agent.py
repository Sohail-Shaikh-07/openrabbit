"""Tests for ArchitectureAgent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.architecture import ArchitectureAgent
from agents.models import ReviewState, Severity


def _make_state(
    diff: str = "diff --git a/api/routes.py\n+from database.models import User",
    architecture_context: list[str] | None = None,
) -> ReviewState:
    pr = MagicMock()
    pr.diff = diff
    retrieval = MagicMock()
    retrieval.architecture = architecture_context or [
        "API layer must not import directly from database layer."
    ]
    return {
        "pr_payload": pr,
        "retrieval_result": retrieval,
        "agent_results": [],
        "error": None,
    }


def _llm_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings})


def test_architecture_agent_name() -> None:
    assert ArchitectureAgent.name == "architecture"


@pytest.mark.asyncio
async def test_architecture_agent_returns_findings() -> None:
    agent = ArchitectureAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "high",
                "file": "api/routes.py",
                "line": 3,
                "confidence": 0.88,
                "title": "Layer violation: API imports from database",
                "reason": "API layer imports directly from database layer, bypassing service layer.",
                "suggestion": "Import from service layer instead.",
                "fix": "from services.user_service import UserService",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].category == "architecture"
    assert result.findings[0].severity == Severity.high


@pytest.mark.asyncio
async def test_architecture_agent_includes_context_in_prompt() -> None:
    agent = ArchitectureAgent()
    arch_context = ["Services must not depend on each other directly."]
    state = _make_state(architecture_context=arch_context)

    captured_prompts: list[str] = []

    async def fake_generate(prompt: str) -> str:
        captured_prompts.append(prompt)
        return _llm_response([])

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=fake_generate)
        await agent.run(state)

    assert len(captured_prompts) == 1
    assert "Services must not depend on each other directly." in captured_prompts[0]


@pytest.mark.asyncio
async def test_architecture_agent_filters_low_confidence() -> None:
    agent = ArchitectureAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "medium",
                "file": "core/utils.py",
                "line": 5,
                "confidence": 0.45,
                "title": "Possible dependency issue",
                "reason": "Might violate layer.",
                "suggestion": "Review.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_architecture_agent_handles_malformed_json() -> None:
    agent = ArchitectureAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value="not json")
        result = await agent.run(state)

    assert result.findings == []
    assert result.agent == "architecture"


@pytest.mark.asyncio
async def test_architecture_agent_handles_llm_error() -> None:
    agent = ArchitectureAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=OSError("no ollama"))
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_architecture_agent_works_without_retrieval_context() -> None:
    agent = ArchitectureAgent()
    pr = MagicMock()
    pr.diff = "diff --git a/app.py"
    state: ReviewState = {
        "pr_payload": pr,
        "retrieval_result": None,
        "agent_results": [],
        "error": None,
    }

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert result.agent == "architecture"
    assert result.findings == []
