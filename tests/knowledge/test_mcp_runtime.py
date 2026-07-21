from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from configs.schema import McpConnectorSettings
from knowledge.connectors import KnowledgeConnectorRequest
from knowledge.mcp_runtime import McpConnectorRuntime


@dataclass
class FakeSession:
    tools: list[str]
    resources: list[str]
    fail_on_call: bool = False

    async def list_tools(self) -> object:
        return SimpleNamespace(tools=[SimpleNamespace(name=tool) for tool in self.tools])

    async def list_resources(self) -> object:
        return SimpleNamespace(
            resources=[SimpleNamespace(uri=resource, name=resource) for resource in self.resources]
        )

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        if self.fail_on_call:
            raise RuntimeError("tool failed")
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=f"Tool {name} saw {arguments['repo']} with token: ghp_secretvalue"
                )
            ]
        )

    async def read_resource(self, uri: str) -> object:
        if self.fail_on_call:
            raise RuntimeError("resource failed")
        return SimpleNamespace(contents=[SimpleNamespace(text=f"Resource body for {uri}")])


class FakeSessionContext(AbstractAsyncContextManager[FakeSession]):
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.servers: list[str] = []

    def __call__(self, server: Any) -> FakeSessionContext:
        self.servers.append(server.name)
        return FakeSessionContext(self.session)


def _settings(body: dict[str, object]) -> McpConnectorSettings:
    return McpConnectorSettings.model_validate(body)


def test_mcp_runtime_disabled_is_fail_open() -> None:
    factory = FakeFactory(FakeSession(tools=["search"], resources=[]))
    runtime = McpConnectorRuntime(_settings({"enabled": False}), session_factory=factory)

    health = runtime.is_available()
    items = runtime.retrieve(KnowledgeConnectorRequest(repo="owner/repo", pr_number=1))

    assert health.available is False
    assert health.reason == "disabled"
    assert items == []
    assert factory.servers == []


def test_mcp_runtime_requires_optional_sdk_without_injected_factory(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("knowledge.mcp_runtime.mcp_sdk_available", lambda: False)
    runtime = McpConnectorRuntime(
        _settings(
            {
                "enabled": True,
                "servers": [
                    {
                        "name": "docs",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_resources": ["docs://architecture"],
                    }
                ],
            }
        )
    )

    health = runtime.is_available()

    assert health.available is False
    assert "optional mcp package is not installed" in health.reason


def test_mcp_runtime_health_rejects_servers_without_allowlists() -> None:
    factory = FakeFactory(FakeSession(tools=["search"], resources=["docs://architecture"]))
    runtime = McpConnectorRuntime(
        _settings(
            {
                "enabled": True,
                "servers": [
                    {
                        "name": "docs",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                    }
                ],
            }
        ),
        session_factory=factory,
    )

    health = runtime.is_available()

    assert health.available is False
    assert "no approved MCP tools or resources configured" in health.reason
    assert factory.servers == []


def test_mcp_runtime_health_checks_tool_and_resource_allowlists() -> None:
    factory = FakeFactory(FakeSession(tools=["search"], resources=["docs://architecture"]))
    runtime = McpConnectorRuntime(
        _settings(
            {
                "enabled": True,
                "servers": [
                    {
                        "name": "docs",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["search"],
                        "allowed_resources": ["docs://architecture"],
                    }
                ],
            }
        ),
        session_factory=factory,
    )

    health = runtime.is_available()

    assert health.available is True
    assert health.reason == "1 MCP server available"
    assert factory.servers == ["docs"]


def test_mcp_runtime_health_reports_missing_allowed_operations() -> None:
    factory = FakeFactory(FakeSession(tools=["lookup"], resources=[]))
    runtime = McpConnectorRuntime(
        _settings(
            {
                "enabled": True,
                "servers": [
                    {
                        "name": "docs",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["search"],
                        "allowed_resources": ["docs://architecture"],
                    }
                ],
            }
        ),
        session_factory=factory,
    )

    health = runtime.is_available()

    assert health.available is False
    assert "docs missing allowed tools: search" in health.reason
    assert "docs missing allowed resources: docs://architecture" in health.reason


def test_mcp_runtime_retrieves_only_allowlisted_context_and_sanitizes_text() -> None:
    factory = FakeFactory(
        FakeSession(tools=["search", "unused"], resources=["docs://architecture"])
    )
    runtime = McpConnectorRuntime(
        _settings(
            {
                "enabled": True,
                "max_items": 5,
                "servers": [
                    {
                        "name": "docs",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["search"],
                        "allowed_resources": ["docs://architecture"],
                    }
                ],
            }
        ),
        session_factory=factory,
    )

    items = runtime.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            head_sha="abc123",
            changed_paths=("src/app.py",),
            query="auth export",
        )
    )

    assert [item.source_id for item in items] == [
        "mcp:docs:resource:docs://architecture",
        "mcp:docs:tool:search",
    ]
    assert all(item.source_kind.value == "mcp" for item in items)
    assert items[0].body == "Resource body for docs://architecture"
    assert "ghp_secretvalue" not in items[1].body
    assert "[REDACTED]" in items[1].body


def test_mcp_runtime_retrieve_fails_open_when_server_operation_fails() -> None:
    factory = FakeFactory(
        FakeSession(tools=["search"], resources=["docs://architecture"], fail_on_call=True)
    )
    runtime = McpConnectorRuntime(
        _settings(
            {
                "enabled": True,
                "servers": [
                    {
                        "name": "docs",
                        "transport": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "allowed_tools": ["search"],
                        "allowed_resources": ["docs://architecture"],
                    }
                ],
            }
        ),
        session_factory=factory,
    )

    items = runtime.retrieve(KnowledgeConnectorRequest(repo="owner/repo", pr_number=42))

    assert items == []
