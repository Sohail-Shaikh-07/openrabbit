"""Tests for SecurityAgent and OllamaClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.llm import OllamaClient, parse_findings
from agents.models import ReviewState, Severity
from agents.security import CONFIDENCE_THRESHOLD, SecurityAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    diff: str = "diff --git a/auth.py\n+password = 'secret'",
    security_context: list[object] | None = None,
) -> ReviewState:
    pr = MagicMock()
    pr.diff = diff
    pr.files = []
    retrieval = MagicMock()
    retrieval.security = security_context or []
    return {
        "pr_payload": pr,
        "retrieval_result": retrieval,
        "agent_results": [],
        "error": None,
    }


def _llm_response(findings: list[dict]) -> str:
    return json.dumps({"findings": findings})


# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_client_posts_to_generate_endpoint() -> None:
    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5-coder:7b")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": "hello"}

    with patch("agents.llm.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await client.generate("test prompt")

    assert result == "hello"
    mock_http.post.assert_called_once()
    call_args = mock_http.post.call_args
    assert "generate" in call_args.args[0]


@pytest.mark.asyncio
async def test_ollama_client_passes_model_and_prompt() -> None:
    client = OllamaClient(base_url="http://localhost:11434", model="my-model")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"response": "ok"}

    with patch("agents.llm.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await client.generate("my prompt")

    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["model"] == "my-model"
    assert payload["prompt"] == "my prompt"
    assert payload["stream"] is False
    assert payload["format"] == "json"


def test_parse_findings_accepts_markdown_json_fence() -> None:
    raw = """```json
{"findings":[{"severity":"high","file":"auth.py","line":10,"confidence":0.91,"title":"Hardcoded secret","reason":"Secret is committed.","suggestion":"Move it to an environment variable.","fix":""}]}
```"""

    findings = parse_findings(raw, "security")

    assert len(findings) == 1
    assert findings[0].title == "Hardcoded secret"


def test_parse_findings_accepts_short_prose_around_json() -> None:
    raw = """Here is the review result:
{"findings":[{"severity":"medium","file":"app.py","line":7,"confidence":0.82,"title":"Missing guard","reason":"Input may be None.","suggestion":"Add a guard clause.","fix":""}]}
Done."""

    findings = parse_findings(raw, "bug")

    assert len(findings) == 1
    assert findings[0].category == "bug"


# ---------------------------------------------------------------------------
# SecurityAgent.name
# ---------------------------------------------------------------------------


def test_security_agent_name() -> None:
    assert SecurityAgent.name == "security"


# ---------------------------------------------------------------------------
# SecurityAgent.run - findings parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_agent_returns_findings_above_threshold() -> None:
    agent = SecurityAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "high",
                "file": "auth.py",
                "line": 10,
                "confidence": 0.90,
                "title": "Hardcoded secret",
                "reason": "Secret in source code.",
                "suggestion": "Use env vars.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].severity == Severity.high
    assert result.findings[0].title == "Hardcoded secret"
    assert result.findings[0].confidence == pytest.approx(0.90)


@pytest.mark.asyncio
async def test_security_agent_filters_low_confidence() -> None:
    agent = SecurityAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "medium",
                "file": "app.py",
                "line": 5,
                "confidence": 0.50,
                "title": "Possible XSS",
                "reason": "Unescaped output.",
                "suggestion": "Escape HTML.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_security_agent_returns_empty_on_no_findings() -> None:
    agent = SecurityAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert result.findings == []
    assert result.agent == "security"


@pytest.mark.asyncio
async def test_security_agent_handles_malformed_json() -> None:
    agent = SecurityAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value="not valid json {{{")
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_security_agent_handles_llm_error() -> None:
    agent = SecurityAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=OSError("connection refused"))
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_security_agent_severity_mapping() -> None:
    agent = SecurityAgent()
    state = _make_state()

    findings_json = _llm_response(
        [
            {
                "severity": "critical",
                "file": "db.py",
                "line": 1,
                "confidence": 0.95,
                "title": "SQL injection",
                "reason": "Unsanitized input.",
                "suggestion": "Use parameterized queries.",
                "fix": "",
            },
            {
                "severity": "low",
                "file": "utils.py",
                "line": 3,
                "confidence": 0.80,
                "title": "Minor issue",
                "reason": "Minor.",
                "suggestion": "Fix it.",
                "fix": "",
            },
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=findings_json)
        result = await agent.run(state)

    severities = {f.title: f.severity for f in result.findings}
    assert severities["SQL injection"] == Severity.critical
    assert severities["Minor issue"] == Severity.low


@pytest.mark.asyncio
async def test_security_agent_includes_project_rules_and_review_discipline() -> None:
    agent = SecurityAgent()
    state = _make_state(
        security_context=[
            {
                "payload": {
                    "source_path": ".openrabbit/security_rules.md",
                    "text": "Never allow disabled TLS verification in production code.",
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

    assert "Never allow disabled TLS verification" in captured[0]
    assert "Do not invent" in captured[0]
    assert "changed lines" in captured[0]


@pytest.mark.asyncio
async def test_security_agent_result_has_execution_time() -> None:
    agent = SecurityAgent()
    state = _make_state()

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert result.execution_time >= 0.0


def test_confidence_threshold_is_at_least_seventy_percent() -> None:
    assert CONFIDENCE_THRESHOLD >= 0.70
