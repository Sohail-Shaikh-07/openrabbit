"""Tests for SecurityAgent and OllamaClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.llm import OllamaClient, OpenAIClient, parse_findings
from agents.models import ReviewState, Severity
from agents.security import CONFIDENCE_THRESHOLD, SecurityAgent
from github_.diff import DiffLine, Hunk

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


def _parsed_file(path: str, hunks: list[Hunk]) -> MagicMock:
    parsed = MagicMock()
    parsed.path = path
    parsed.status = "modified"
    parsed.is_binary = False
    parsed.hunks = hunks
    return parsed


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


def test_ollama_client_default_timeout_is_generous_for_local_models() -> None:
    client = OllamaClient()

    assert client.timeout >= 300.0


def test_ollama_client_exposes_provider_metadata() -> None:
    client = OllamaClient(model="openrabbit-reviewer-v1")

    assert client.provider_name == "ollama"
    assert client.model_name == "openrabbit-reviewer-v1"


def test_ollama_client_timeout_can_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENRABBIT_OLLAMA_TIMEOUT_SECONDS", "240")
    client = OllamaClient()

    assert client.timeout == 240.0


@pytest.mark.asyncio
async def test_openai_client_posts_chat_completion_request() -> None:
    client = OpenAIClient(api_key="sk-test", model="gpt-4.1-mini")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"findings":[]}'}}],
    }

    with patch("agents.llm.httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await client.generate("review prompt")

    assert result == '{"findings":[]}'
    mock_http.post.assert_called_once()
    call_args = mock_http.post.call_args
    assert call_args.args[0] == "https://api.openai.com/v1/chat/completions"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"
    payload = call_args.kwargs["json"]
    assert payload["model"] == "gpt-4.1-mini"
    assert payload["messages"] == [{"role": "user", "content": "review prompt"}]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0


def test_openai_client_exposes_provider_metadata_without_key() -> None:
    client = OpenAIClient(api_key="sk-secret", model="gpt-4.1-mini")

    assert client.provider_name == "openai"
    assert client.model_name == "gpt-4.1-mini"
    assert "sk-secret" not in repr(client)


def test_openai_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        OpenAIClient(api_key=" ", model="gpt-4.1-mini")


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
async def test_security_agent_flags_raw_sql_f_string_even_when_llm_is_silent() -> None:
    agent = SecurityAgent()
    state = _make_state()
    pr = state["pr_payload"]
    pr.files = [
        _parsed_file(
            "app/repositories/task_repository.py",
            [
                Hunk(
                    old_start=67,
                    old_lines=1,
                    new_start=67,
                    new_lines=2,
                    lines=[
                        DiffLine(
                            kind="addition",
                            text='count_sql = text(f"SELECT COUNT(*) FROM tasks WHERE {where_clause}")',
                        ),
                    ],
                )
            ],
        )
    ]

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].title == "Raw SQL construction from changed input"
    assert result.findings[0].file == "app/repositories/task_repository.py"
    assert result.findings[0].line == 67


@pytest.mark.asyncio
async def test_security_agent_deduplicates_preflight_when_model_finds_same_sql_issue() -> None:
    agent = SecurityAgent()
    state = _make_state()
    pr = state["pr_payload"]
    pr.files = [
        _parsed_file(
            "app/repositories/task_repository.py",
            [
                Hunk(
                    old_start=74,
                    old_lines=1,
                    new_start=74,
                    new_lines=8,
                    lines=[
                        DiffLine(
                            kind="addition",
                            text="where_clause = f\"title LIKE '%{query}%'\"",
                        ),
                        DiffLine(kind="context", text="count_sql = text(...)"),
                        DiffLine(kind="context", text="total = self.session.execute(count_sql)"),
                        DiffLine(kind="context", text="tasks = []"),
                        DiffLine(kind="context", text="if total:"),
                        DiffLine(kind="context", text="    pass"),
                        DiffLine(kind="context", text="rows_sql = text("),
                        DiffLine(
                            kind="addition",
                            text='rows_sql = text(f"SELECT * FROM tasks WHERE {where_clause}")',
                        ),
                    ],
                )
            ],
        )
    ]
    model_response = _llm_response(
        [
            {
                "severity": "high",
                "file": "app/repositories/task_repository.py",
                "line": 74,
                "confidence": 0.90,
                "title": "SQL Injection via String Formatting",
                "reason": "The changed string formatting can alter the query.",
                "suggestion": "Use parameterized queries.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=model_response)
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].title == "SQL Injection via String Formatting"


@pytest.mark.asyncio
async def test_security_agent_deduplicates_nearby_model_findings_for_same_sql_issue() -> None:
    agent = SecurityAgent()
    state = _make_state()
    pr = state["pr_payload"]
    pr.files = [
        _parsed_file(
            "app/repositories/task_repository.py",
            [
                Hunk(
                    old_start=71,
                    old_lines=1,
                    new_start=71,
                    new_lines=13,
                    lines=[
                        DiffLine(
                            kind="addition",
                            text='count_sql = text(f"SELECT COUNT(*) FROM tasks WHERE {where_clause}")',
                        ),
                    ],
                )
            ],
        )
    ]
    model_response = _llm_response(
        [
            {
                "severity": "high",
                "file": "app/repositories/task_repository.py",
                "line": line,
                "confidence": 0.90,
                "title": "SQL Injection Vulnerability in Advanced Search",
                "reason": "The query string is built with f-strings.",
                "suggestion": "Use parameterized queries.",
                "fix": "",
            }
            for line in (71, 80, 82, 83)
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=model_response)
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].line == 71


@pytest.mark.asyncio
async def test_security_agent_drops_model_sql_finding_without_changed_raw_sql_sink() -> None:
    agent = SecurityAgent()
    state = _make_state()
    pr = state["pr_payload"]
    pr.files = [
        _parsed_file(
            "app/api/routes/tasks.py",
            [
                Hunk(
                    old_start=80,
                    old_lines=1,
                    new_start=80,
                    new_lines=2,
                    lines=[
                        DiffLine(kind="addition", text="task.owner = payload.owner"),
                        DiffLine(kind="addition", text="session.commit()"),
                    ],
                )
            ],
        )
    ]
    model_response = _llm_response(
        [
            {
                "severity": "medium",
                "file": "app/api/routes/tasks.py",
                "line": 80,
                "confidence": 0.88,
                "title": "Potential SQL Injection",
                "reason": "The owner field is assigned from user input.",
                "suggestion": "Sanitize owner before assignment.",
                "fix": "",
            }
        ]
    )

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=model_response)
        result = await agent.run(state)

    assert result.findings == []


@pytest.mark.asyncio
async def test_security_agent_flags_admin_route_without_authorization_when_llm_is_silent() -> None:
    agent = SecurityAgent()
    state = _make_state()
    pr = state["pr_payload"]
    pr.files = [
        _parsed_file(
            "app/api/routes/tasks.py",
            [
                Hunk(
                    old_start=66,
                    old_lines=1,
                    new_start=66,
                    new_lines=6,
                    lines=[
                        DiffLine(kind="addition", text='@router.post("/admin/{task_id}/reassign")'),
                        DiffLine(kind="addition", text="def admin_reassign_task("),
                        DiffLine(
                            kind="addition", text="_: Annotated[str, Depends(require_subject)],"
                        ),
                        DiffLine(kind="addition", text="task.owner = payload.owner"),
                    ],
                )
            ],
        )
    ]

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(return_value=_llm_response([]))
        result = await agent.run(state)

    assert len(result.findings) == 1
    assert result.findings[0].title == "Admin route lacks an authorization check"
    assert result.findings[0].file == "app/api/routes/tasks.py"
    assert result.findings[0].line == 66


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
    assert "Authentication-only checks do not prove authorization" in captured[0]
    assert "Do not label plain assignment as SQL injection" in captured[0]


@pytest.mark.asyncio
async def test_security_agent_includes_changed_line_evidence() -> None:
    agent = SecurityAgent()
    state = _make_state()
    pr = state["pr_payload"]
    pr.files = [
        _parsed_file(
            "app/repositories/task_repository.py",
            [
                Hunk(
                    old_start=67,
                    old_lines=2,
                    new_start=67,
                    new_lines=4,
                    lines=[
                        DiffLine(kind="context", text="def advanced_search(...):"),
                        DiffLine(
                            kind="addition",
                            text='count_sql = text(f"SELECT COUNT(*) FROM tasks WHERE {where_clause}")',
                        ),
                    ],
                )
            ],
        )
    ]
    captured: list[str] = []

    async def fake_generate(prompt: str) -> str:
        captured.append(prompt)
        return _llm_response([])

    with patch.object(agent, "_client") as mock_client:
        mock_client.generate = AsyncMock(side_effect=fake_generate)
        await agent.run(state)

    assert "Changed-line evidence:" in captured[0]
    assert "app/repositories/task_repository.py (modified)" in captured[0]
    assert '+68 count_sql = text(f"SELECT COUNT(*) FROM tasks WHERE {where_clause}")' in captured[0]


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
