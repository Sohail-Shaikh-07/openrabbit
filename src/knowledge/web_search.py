"""MCP-backed web search knowledge connector."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from configs.schema import McpConnectorSettings, McpServerSettings, WebSearchConnectorSettings
from knowledge.connectors import (
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)
from knowledge.mcp_runtime import (
    McpSessionFactory,
    _extract_names,
    _extract_text,
    _open_session,
    _run_async,
    _with_timeout,
    mcp_sdk_available,
)

_CODE_LIKE_PATTERNS = (
    re.compile(r"\b(def|class|import|from|return|async|await)\b"),
    re.compile(r"\b(function|const|let|var|export|interface|type)\b"),
    re.compile(r"[{};]"),
    re.compile(r"=>|::|\\n"),
)


@dataclass(frozen=True)
class _SelectedSearchTool:
    server: McpServerSettings
    tool: str


class McpWebSearchConnector:
    """Optional web search flow routed through a configured MCP server."""

    name = "web_search"
    source_kind = KnowledgeSourceKind.WEB_SEARCH

    def __init__(
        self,
        settings: WebSearchConnectorSettings,
        mcp_settings: McpConnectorSettings,
        *,
        session_factory: McpSessionFactory | None = None,
    ) -> None:
        self._settings = settings
        self._mcp_settings = mcp_settings
        self._session_factory = session_factory

    def is_available(self) -> KnowledgeConnectorHealth:
        """Return non-fatal web search availability."""
        selected = self._select_tool()
        if isinstance(selected, KnowledgeConnectorHealth):
            return selected
        if self._session_factory is None and not mcp_sdk_available():
            return KnowledgeConnectorHealth(
                name=self.name,
                source_kind=self.source_kind,
                available=False,
                reason="optional mcp package is not installed",
            )

        health = _run_fail_open(
            self._check_tool(selected.server, selected.tool),
            fallback_reason=f"{selected.server.name} unavailable",
        )
        return KnowledgeConnectorHealth(
            name=self.name,
            source_kind=self.source_kind,
            available=health.available,
            reason=health.reason,
        )

    def retrieve(self, request: KnowledgeConnectorRequest) -> Sequence[KnowledgeItem]:
        """Return source-labeled public web search results, or an empty list."""
        selected = self._select_tool()
        if isinstance(selected, KnowledgeConnectorHealth):
            return []
        query = _build_query(request, allow_private=self._settings.allow_private_code_queries)
        if not query:
            return []

        max_items = min(request.max_items, self._settings.max_items)
        arguments = _tool_arguments(
            request,
            query=query,
            max_items=max_items,
            allow_private=self._settings.allow_private_code_queries,
        )
        items = _run_items_fail_open(
            _with_timeout(
                self._call_search_tool(
                    selected.server,
                    selected.tool,
                    arguments=arguments,
                    max_items=max_items,
                ),
                timeout_seconds=selected.server.timeout_seconds,
            )
        )
        return normalize_knowledge_items(items, max_items=max_items)

    def _select_tool(self) -> _SelectedSearchTool | KnowledgeConnectorHealth:
        if not self._settings.enabled:
            return _health(False, "disabled")
        if not self._settings.mcp_server:
            return _health(False, "no MCP server selected for web search")
        if not self._mcp_settings.enabled:
            return _health(False, "MCP connector is disabled")

        server = next(
            (item for item in self._mcp_settings.servers if item.name == self._settings.mcp_server),
            None,
        )
        if server is None:
            return _health(False, f"configured MCP server not found: {self._settings.mcp_server}")
        if not server.allowed_tools:
            return _health(
                False,
                f"{server.name} has no approved MCP web search tools configured",
            )
        return _SelectedSearchTool(server=server, tool=server.allowed_tools[0])

    async def _check_tool(self, server: McpServerSettings, tool: str) -> KnowledgeConnectorHealth:
        async with _open_session(server, self._session_factory) as session:
            tools_result = await asyncio.wait_for(
                session.list_tools(), timeout=server.timeout_seconds
            )
        available_tools = _extract_names(tools_result, "tools")
        if tool not in available_tools:
            return _health(False, f"{server.name} missing allowed web search tool: {tool}")
        return _health(True, "configured")

    async def _call_search_tool(
        self,
        server: McpServerSettings,
        tool: str,
        *,
        arguments: dict[str, object],
        max_items: int,
    ) -> list[KnowledgeItem]:
        async with _open_session(server, self._session_factory) as session:
            result = await asyncio.wait_for(
                session.call_tool(tool, arguments=arguments),
                timeout=server.timeout_seconds,
            )
        records = _extract_result_records(result)
        if not records:
            text = _extract_text(result)
            if not text:
                return []
            records = [{"title": f"Web search result from {server.name}", "body": text}]

        items = [
            _knowledge_item(server=server.name, tool=tool, index=index, record=record)
            for index, record in enumerate(records[:max_items], start=1)
        ]
        return items


def _health(available: bool, reason: str) -> KnowledgeConnectorHealth:
    return KnowledgeConnectorHealth(
        name="web_search",
        source_kind=KnowledgeSourceKind.WEB_SEARCH,
        available=available,
        reason=reason,
    )


def _run_fail_open(
    coroutine: Coroutine[Any, Any, KnowledgeConnectorHealth],
    *,
    fallback_reason: str,
) -> KnowledgeConnectorHealth:
    try:
        return _run_async(coroutine)
    except Exception as exc:
        reason = sanitize_knowledge_text(str(exc), max_chars=180)
        return _health(False, f"{fallback_reason}: {reason}" if reason else fallback_reason)


def _run_items_fail_open(
    coroutine: Coroutine[Any, Any, list[KnowledgeItem]],
) -> list[KnowledgeItem]:
    try:
        return _run_async(coroutine)
    except Exception:
        return []


def _build_query(request: KnowledgeConnectorRequest, *, allow_private: bool) -> str:
    query = sanitize_knowledge_text(request.query, max_chars=500)
    if not query:
        if not allow_private:
            return ""
        query = sanitize_knowledge_text(
            " ".join((*request.changed_paths, *request.changed_symbols)),
            max_chars=500,
        )
    if not allow_private and _looks_like_private_code(query):
        return ""
    return query


def _looks_like_private_code(query: str) -> bool:
    return any(pattern.search(query) for pattern in _CODE_LIKE_PATTERNS)


def _tool_arguments(
    request: KnowledgeConnectorRequest,
    *,
    query: str,
    max_items: int,
    allow_private: bool,
) -> dict[str, object]:
    arguments: dict[str, object] = {"query": query, "max_results": max_items}
    if allow_private:
        arguments.update(
            {
                "repo": request.repo,
                "pr_number": request.pr_number,
                "changed_paths": list(request.changed_paths),
                "changed_symbols": list(request.changed_symbols),
            }
        )
    return arguments


def _extract_result_records(result: object) -> list[Mapping[str, object]]:
    container = _extract_result_container(result)
    if container is None:
        return []
    records: list[Mapping[str, object]] = []
    for item in _as_sequence(container):
        record = _record_from_object(item)
        if record:
            records.append(record)
    return records


def _extract_result_container(result: object) -> object | None:
    if isinstance(result, Mapping):
        return result.get("results") or result.get("items") or result.get("data")
    for attr in ("results", "items", "data"):
        if hasattr(result, attr):
            value: object = getattr(result, attr)
            return value
    return None


def _record_from_object(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    record: dict[str, object] = {}
    for attr in ("title", "url", "link", "snippet", "content", "text", "body", "score"):
        if hasattr(value, attr):
            record[attr] = getattr(value, attr)
    return record


def _knowledge_item(
    *,
    server: str,
    tool: str,
    index: int,
    record: Mapping[str, object],
) -> KnowledgeItem:
    url = _record_text(record, "url") or _record_text(record, "link")
    title = _record_text(record, "title") or url or f"Web search result {index}"
    body = (
        _record_text(record, "snippet")
        or _record_text(record, "content")
        or _record_text(record, "text")
        or _record_text(record, "body")
        or title
    )
    return KnowledgeItem(
        source_id=f"web_search:{server}:{tool}:{_source_suffix(index=index, url=url, title=title)}",
        source_kind=KnowledgeSourceKind.WEB_SEARCH,
        title=title,
        body=body,
        url=url,
        score=_record_score(record.get("score")),
        metadata={"server": server, "tool": tool, "trust": "untrusted"},
    )


def _record_text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    return value.strip() if isinstance(value, str) else ""


def _record_score(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _source_suffix(*, index: int, url: str, title: str) -> str:
    source = url or title or str(index)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{index}:{digest}"


def _as_sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return [value]
