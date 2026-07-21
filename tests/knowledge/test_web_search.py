from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from configs.schema import KnowledgeConnectorsSettings
from knowledge.connectors import KnowledgeConnectorRequest
from knowledge.web_search import McpWebSearchConnector


@dataclass
class FakeWebSearchSession:
    tools: list[str]
    response: object
    calls: list[dict[str, object]] = field(default_factory=list)
    fail_on_call: bool = False

    async def list_tools(self) -> object:
        return SimpleNamespace(tools=[SimpleNamespace(name=tool) for tool in self.tools])

    async def list_resources(self) -> object:
        return SimpleNamespace(resources=[])

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append({"name": name, "arguments": arguments})
        if self.fail_on_call:
            raise RuntimeError("search failed")
        return self.response

    async def read_resource(self, uri: str) -> object:
        raise AssertionError(f"web search should not read resources: {uri}")


class FakeWebSearchContext(AbstractAsyncContextManager[FakeWebSearchSession]):
    def __init__(self, session: FakeWebSearchSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeWebSearchSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeFactory:
    def __init__(self, session: FakeWebSearchSession) -> None:
        self.session = session
        self.servers: list[str] = []

    def __call__(self, server: Any) -> FakeWebSearchContext:
        self.servers.append(server.name)
        return FakeWebSearchContext(self.session)


def _connector(
    body: dict[str, object],
    *,
    session: FakeWebSearchSession | None = None,
) -> McpWebSearchConnector:
    settings = KnowledgeConnectorsSettings.model_validate(body)
    return McpWebSearchConnector(
        settings.web_search,
        settings.mcp,
        session_factory=FakeFactory(session) if session else None,
    )


def test_web_search_disabled_is_fail_open() -> None:
    connector = _connector({"web_search": {"enabled": False}})

    health = connector.is_available()
    items = connector.retrieve(KnowledgeConnectorRequest(repo="owner/repo", pr_number=1))

    assert health.available is False
    assert health.reason == "disabled"
    assert items == []


def test_web_search_requires_selected_mcp_server() -> None:
    connector = _connector({"web_search": {"enabled": True}})

    health = connector.is_available()

    assert health.available is False
    assert health.reason == "no MCP server selected for web search"


def test_web_search_reports_disabled_mcp_connector() -> None:
    connector = _connector({"web_search": {"enabled": True, "mcp_server": "search"}})

    health = connector.is_available()

    assert health.available is False
    assert health.reason == "MCP connector is disabled"


def test_web_search_reports_missing_selected_server() -> None:
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "docs",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["search"],
                    }
                ],
            },
            "web_search": {"enabled": True, "mcp_server": "search"},
        }
    )

    health = connector.is_available()

    assert health.available is False
    assert health.reason == "configured MCP server not found: search"


def test_web_search_requires_allowed_tool_on_selected_server() -> None:
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_resources": ["docs://architecture"],
                    }
                ],
            },
            "web_search": {"enabled": True, "mcp_server": "search"},
        }
    )

    health = connector.is_available()

    assert health.available is False
    assert health.reason == "search has no approved MCP web search tools configured"


def test_web_search_health_checks_configured_mcp_tool() -> None:
    session = FakeWebSearchSession(
        tools=["web_search"],
        response={"results": []},
    )
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["web_search"],
                    }
                ],
            },
            "web_search": {"enabled": True, "mcp_server": "search"},
        },
        session=session,
    )

    health = connector.is_available()

    assert health.available is True
    assert health.reason == "configured"


def test_web_search_retrieves_source_labeled_results() -> None:
    session = FakeWebSearchSession(
        tools=["web_search"],
        response={
            "results": [
                {
                    "title": "FastAPI security docs",
                    "url": "https://fastapi.tiangolo.com/tutorial/security/",
                    "snippet": "Use dependency injection for auth. token: ghp_secretvalue",
                    "score": 0.9,
                },
                {
                    "title": "OWASP authorization guide",
                    "link": "https://owasp.org/www-project-top-ten/",
                    "content": "Authorization controls should be enforced server side.",
                    "score": 0.7,
                },
            ]
        },
    )
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["web_search"],
                    }
                ],
            },
            "web_search": {"enabled": True, "mcp_server": "search", "max_items": 2},
        },
        session=session,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            query="FastAPI authorization docs",
            max_items=8,
        )
    )

    assert [item.source_kind.value for item in items] == ["web_search", "web_search"]
    assert items[0].source_id.startswith("web_search:search:web_search:")
    assert items[0].url == "https://fastapi.tiangolo.com/tutorial/security/"
    assert items[0].metadata["server"] == "search"
    assert items[0].metadata["tool"] == "web_search"
    assert items[0].metadata["trust"] == "untrusted"
    assert "ghp_secretvalue" not in items[0].body
    assert "[REDACTED]" in items[0].body
    assert session.calls == [
        {
            "name": "web_search",
            "arguments": {"query": "FastAPI authorization docs", "max_results": 2},
        }
    ]


def test_web_search_uses_text_result_when_tool_returns_unstructured_content() -> None:
    session = FakeWebSearchSession(
        tools=["web_search"],
        response=SimpleNamespace(content=[SimpleNamespace(text="Result text without a URL")]),
    )
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["web_search"],
                    }
                ],
            },
            "web_search": {"enabled": True, "mcp_server": "search"},
        },
        session=session,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="pytest docs")
    )

    assert len(items) == 1
    assert items[0].title == "Web search result from search"
    assert items[0].body == "Result text without a URL"


def test_web_search_blocks_code_like_queries_by_default() -> None:
    session = FakeWebSearchSession(tools=["web_search"], response={"results": []})
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["web_search"],
                    }
                ],
            },
            "web_search": {"enabled": True, "mcp_server": "search"},
        },
        session=session,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            query="def export_admin_token(): return request.headers['Authorization']",
        )
    )

    assert items == []
    assert session.calls == []


def test_web_search_allows_private_code_queries_when_explicitly_enabled() -> None:
    session = FakeWebSearchSession(
        tools=["web_search"],
        response={"results": [{"title": "Python docs", "url": "https://docs.python.org/3/"}]},
    )
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["web_search"],
                    }
                ],
            },
            "web_search": {
                "enabled": True,
                "mcp_server": "search",
                "allow_private_code_queries": True,
            },
        },
        session=session,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            changed_paths=("src/api.py",),
            query="def route(): pass",
        )
    )

    assert len(items) == 1
    assert session.calls[0]["arguments"] == {
        "query": "def route(): pass",
        "max_results": 5,
        "repo": "owner/repo",
        "pr_number": 42,
        "changed_paths": ["src/api.py"],
        "changed_symbols": [],
    }


def test_web_search_fails_open_when_mcp_tool_fails() -> None:
    session = FakeWebSearchSession(
        tools=["web_search"],
        response={"results": []},
        fail_on_call=True,
    )
    connector = _connector(
        {
            "mcp": {
                "enabled": True,
                "servers": [
                    {
                        "name": "search",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["web_search"],
                    }
                ],
            },
            "web_search": {"enabled": True, "mcp_server": "search"},
        },
        session=session,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="security docs")
    )

    assert items == []
