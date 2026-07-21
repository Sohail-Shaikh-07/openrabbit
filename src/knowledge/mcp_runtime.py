"""Runtime boundary for configured MCP knowledge servers."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from collections.abc import AsyncIterator, Coroutine, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from configs.schema import McpConnectorSettings, McpServerSettings
from knowledge.connectors import (
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)


class McpSession(Protocol):
    """Subset of MCP client session methods used by OpenRabbit."""

    async def list_tools(self) -> object: ...

    async def list_resources(self) -> object: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object: ...

    async def read_resource(self, uri: str) -> object: ...


class McpSessionFactory(Protocol):
    """Creates an async MCP session context for one configured server."""

    def __call__(self, server: McpServerSettings) -> object: ...


@dataclass(frozen=True)
class McpServerHealth:
    """Read-only health for one configured MCP server."""

    name: str
    available: bool
    reason: str


class McpConnectorRuntime:
    """Safe runtime wrapper for configured MCP knowledge sources."""

    name = "mcp"
    source_kind = KnowledgeSourceKind.MCP

    def __init__(
        self,
        settings: McpConnectorSettings,
        *,
        session_factory: McpSessionFactory | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    def is_available(self) -> KnowledgeConnectorHealth:
        """Return aggregate MCP availability without raising."""
        if not self._settings.enabled:
            return KnowledgeConnectorHealth(
                name=self.name,
                source_kind=self.source_kind,
                available=False,
                reason="disabled",
            )
        if not self._settings.servers:
            return KnowledgeConnectorHealth(
                name=self.name,
                source_kind=self.source_kind,
                available=False,
                reason="no MCP servers configured",
            )
        if self._session_factory is None and not mcp_sdk_available():
            return KnowledgeConnectorHealth(
                name=self.name,
                source_kind=self.source_kind,
                available=False,
                reason="optional mcp package is not installed",
            )

        health = self.check_server_health()
        available = [item for item in health if item.available]
        if available:
            label = "server" if len(available) == 1 else "servers"
            return KnowledgeConnectorHealth(
                name=self.name,
                source_kind=self.source_kind,
                available=True,
                reason=f"{len(available)} MCP {label} available",
            )

        reason = "; ".join(item.reason for item in health) or "no MCP servers available"
        return KnowledgeConnectorHealth(
            name=self.name,
            source_kind=self.source_kind,
            available=False,
            reason=reason,
        )

    def check_server_health(self) -> list[McpServerHealth]:
        """Read available tool/resource catalogs for configured MCP servers."""
        if not self._settings.enabled:
            return []
        return [
            self._run_with_fail_open(
                _with_timeout(self._check_server(server), timeout_seconds=server.timeout_seconds),
                server.name,
            )
            for server in self._settings.servers
        ]

    def retrieve(self, request: KnowledgeConnectorRequest) -> Sequence[KnowledgeItem]:
        """Return sanitized MCP snippets, or an empty list when MCP is unavailable."""
        if not self.is_available().available:
            return []
        max_items = min(request.max_items, self._settings.max_items)
        items: list[KnowledgeItem] = []
        for server in self._settings.servers:
            if len(items) >= max_items:
                break
            if not _has_allowed_operations(server):
                continue
            server_items = self._run_items_with_fail_open(
                _with_timeout(
                    self._retrieve_from_server(server, request, max_items=max_items - len(items)),
                    timeout_seconds=server.timeout_seconds,
                )
            )
            items.extend(server_items)
        return normalize_knowledge_items(items, max_items=max_items)

    async def _check_server(self, server: McpServerSettings) -> McpServerHealth:
        if not _has_allowed_operations(server):
            return McpServerHealth(
                name=server.name,
                available=False,
                reason=f"{server.name} has no approved MCP tools or resources configured",
            )
        async with _open_session(server, self._session_factory) as session:
            tools_result = await asyncio.wait_for(
                session.list_tools(), timeout=server.timeout_seconds
            )
            resources_result = await asyncio.wait_for(
                session.list_resources(), timeout=server.timeout_seconds
            )

        missing_tools = sorted(set(server.allowed_tools) - _extract_names(tools_result, "tools"))
        missing_resources = sorted(
            set(server.allowed_resources) - _extract_names(resources_result, "resources", key="uri")
        )
        reasons: list[str] = []
        if missing_tools:
            reasons.append(f"{server.name} missing allowed tools: {', '.join(missing_tools)}")
        if missing_resources:
            reasons.append(
                f"{server.name} missing allowed resources: {', '.join(missing_resources)}"
            )
        if reasons:
            return McpServerHealth(name=server.name, available=False, reason="; ".join(reasons))
        return McpServerHealth(name=server.name, available=True, reason="available")

    async def _retrieve_from_server(
        self,
        server: McpServerSettings,
        request: KnowledgeConnectorRequest,
        *,
        max_items: int,
    ) -> list[KnowledgeItem]:
        items: list[KnowledgeItem] = []
        arguments = _tool_arguments(request)
        async with _open_session(server, self._session_factory) as session:
            for resource in server.allowed_resources:
                if len(items) >= max_items:
                    return items
                result = await asyncio.wait_for(
                    session.read_resource(resource), timeout=server.timeout_seconds
                )
                items.append(
                    _knowledge_item(
                        server=server.name,
                        operation=f"resource:{resource}",
                        title=f"{server.name} resource {resource}",
                        body=_extract_text(result),
                        url=resource,
                    )
                )
            for tool in server.allowed_tools:
                if len(items) >= max_items:
                    return items
                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=arguments),
                    timeout=server.timeout_seconds,
                )
                items.append(
                    _knowledge_item(
                        server=server.name,
                        operation=f"tool:{tool}",
                        title=f"{server.name} tool {tool}",
                        body=_extract_text(result),
                    )
                )
        return items

    def _run_with_fail_open(
        self,
        coroutine: Coroutine[Any, Any, McpServerHealth],
        server_name: str,
    ) -> McpServerHealth:
        try:
            return _run_async(coroutine)
        except TimeoutError:
            return McpServerHealth(
                name=server_name,
                available=False,
                reason=f"{server_name} timed out",
            )
        except Exception as exc:
            return McpServerHealth(
                name=server_name,
                available=False,
                reason=f"{server_name} unavailable: {sanitize_knowledge_text(str(exc), max_chars=180)}",
            )

    def _run_items_with_fail_open(
        self, coroutine: Coroutine[Any, Any, list[KnowledgeItem]]
    ) -> list[KnowledgeItem]:
        try:
            return _run_async(coroutine)
        except Exception:
            return []


def mcp_sdk_available() -> bool:
    """Return whether the optional MCP Python SDK can be imported."""
    return importlib.util.find_spec("mcp") is not None


@asynccontextmanager
async def _open_session(
    server: McpServerSettings,
    session_factory: McpSessionFactory | None,
) -> AsyncIterator[McpSession]:
    if session_factory is not None:
        async with session_factory(server) as session:  # type: ignore[attr-defined]
            yield session
        return

    mcp_module: Any = importlib.import_module("mcp")
    client_session = mcp_module.ClientSession

    if server.transport == "stdio":
        stdio_module: Any = importlib.import_module("mcp.client.stdio")
        stdio_parameters = mcp_module.StdioServerParameters
        stdio_client = stdio_module.stdio_client
        params = stdio_parameters(command=server.command, args=list(server.args))
        async with stdio_client(params) as streams:
            read_stream, write_stream = streams
            async with client_session(read_stream, write_stream) as session:
                await session.initialize()
                yield session
        return

    http_module: Any = importlib.import_module("mcp.client.streamable_http")
    streamable_http_client = http_module.streamablehttp_client
    async with streamable_http_client(server.url) as streams:
        read_stream, write_stream = streams[0], streams[1]
        async with client_session(read_stream, write_stream) as session:
            await session.initialize()
            yield session


def _run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future: Future[T] = executor.submit(asyncio.run, coroutine)
        return future.result()


async def _with_timeout[T](
    coroutine: Coroutine[Any, Any, T],
    *,
    timeout_seconds: int,
) -> T:
    return await asyncio.wait_for(coroutine, timeout=timeout_seconds)


def _has_allowed_operations(server: McpServerSettings) -> bool:
    return bool(server.allowed_tools or server.allowed_resources)


def _extract_names(result: object, collection_name: str, *, key: str = "name") -> set[str]:
    collection = getattr(result, collection_name, None)
    if collection is None and isinstance(result, Mapping):
        collection = result.get(collection_name)
    if collection is None:
        return set()
    names: set[str] = set()
    for item in _as_sequence(collection):
        value = getattr(item, key, None)
        if value is None and isinstance(item, Mapping):
            value = item.get(key)
        text = str(value).strip() if value is not None else ""
        if text:
            names.add(text)
    return names


def _tool_arguments(request: KnowledgeConnectorRequest) -> dict[str, object]:
    query = request.query or " ".join((*request.changed_paths, *request.changed_symbols))
    return {
        "query": query.strip(),
        "repo": request.repo,
        "pr_number": request.pr_number,
        "head_sha": request.head_sha,
        "changed_paths": list(request.changed_paths),
        "changed_symbols": list(request.changed_symbols),
        "metadata": dict(request.metadata),
    }


def _knowledge_item(
    *,
    server: str,
    operation: str,
    title: str,
    body: str,
    url: str = "",
) -> KnowledgeItem:
    return KnowledgeItem(
        source_id=f"mcp:{server}:{operation}",
        source_kind=KnowledgeSourceKind.MCP,
        title=title,
        body=body,
        url=url,
        metadata={"server": server, "operation": operation, "trust": "untrusted"},
    )


def _extract_text(value: object) -> str:
    parts = _extract_text_parts(value)
    return "\n".join(part for part in parts if part.strip())


def _extract_text_parts(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, bytes):
        return []
    if isinstance(value, Mapping):
        mapping_parts: list[str] = []
        for key in ("text", "content", "contents", "body", "value"):
            if key in value:
                mapping_parts.extend(_extract_text_parts(value[key]))
        return mapping_parts
    if isinstance(value, Sequence):
        sequence_parts: list[str] = []
        for item in value:
            sequence_parts.extend(_extract_text_parts(item))
        return sequence_parts

    object_parts: list[str] = []
    for attr in ("text", "content", "contents", "body", "value"):
        if hasattr(value, attr):
            object_parts.extend(_extract_text_parts(getattr(value, attr)))
    if object_parts:
        return object_parts
    return []


def _as_sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return [value]
