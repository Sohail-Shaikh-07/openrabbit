"""Configuration-backed knowledge connector registry."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from configs.settings import Settings
from knowledge.connectors import KnowledgeSourceKind
from knowledge.mcp_runtime import McpConnectorRuntime


class ConnectorHealthProvider(Protocol):
    """Small protocol for configured connector health checks."""

    def check_health(self, env: dict[str, str] | None = None) -> ConnectorHealthResult: ...


@dataclass(frozen=True)
class ConnectorHealthResult:
    """Read-only health state for one configured connector."""

    name: str
    enabled: bool
    available: bool
    source_kind: KnowledgeSourceKind
    reason: str


class KnowledgeConnectorRegistry:
    """Registry for optional connector configuration and health checks."""

    def __init__(self, providers: list[ConnectorHealthProvider]) -> None:
        self._providers = providers

    def check_health(self, env: dict[str, str] | None = None) -> list[ConnectorHealthResult]:
        """Return deterministic health for every known connector."""
        return [provider.check_health(env=env) for provider in self._providers]


def build_connector_registry(settings: Settings) -> KnowledgeConnectorRegistry:
    """Build the connector registry from validated settings."""
    connectors = settings.knowledge.connectors
    return KnowledgeConnectorRegistry(
        [
            _StaticConnectorHealthProvider(
                name="mcp",
                source_kind=KnowledgeSourceKind.MCP,
                enabled=connectors.mcp.enabled,
                configured=bool(connectors.mcp.servers),
                missing_reason="no MCP servers configured",
                runtime=McpConnectorRuntime(connectors.mcp),
            ),
            _StaticConnectorHealthProvider(
                name="web_search",
                source_kind=KnowledgeSourceKind.WEB_SEARCH,
                enabled=connectors.web_search.enabled,
                configured=bool(connectors.web_search.mcp_server),
                missing_reason="no MCP server selected for web search",
            ),
            _StaticConnectorHealthProvider(
                name="multi_repo",
                source_kind=KnowledgeSourceKind.MULTI_REPO,
                enabled=connectors.multi_repo.enabled,
                configured=bool(connectors.multi_repo.repositories),
                missing_reason="no repositories configured",
            ),
            _TokenConnectorHealthProvider(
                name="jira",
                source_kind=KnowledgeSourceKind.ISSUE_TRACKER,
                enabled=connectors.jira.enabled,
                token_env=connectors.jira.token_env,
                has_required_metadata=bool(connectors.jira.base_url),
                missing_metadata_reason="no Jira base_url configured",
            ),
            _TokenConnectorHealthProvider(
                name="linear",
                source_kind=KnowledgeSourceKind.ISSUE_TRACKER,
                enabled=connectors.linear.enabled,
                token_env=connectors.linear.token_env,
                has_required_metadata=True,
                missing_metadata_reason="",
            ),
        ]
    )


@dataclass(frozen=True)
class _StaticConnectorHealthProvider:
    name: str
    source_kind: KnowledgeSourceKind
    enabled: bool
    configured: bool
    missing_reason: str
    runtime: McpConnectorRuntime | None = None

    def check_health(self, env: dict[str, str] | None = None) -> ConnectorHealthResult:
        del env
        if not self.enabled:
            return ConnectorHealthResult(
                name=self.name,
                enabled=False,
                available=False,
                source_kind=self.source_kind,
                reason="disabled",
            )
        if not self.configured:
            return ConnectorHealthResult(
                name=self.name,
                enabled=True,
                available=False,
                source_kind=self.source_kind,
                reason=self.missing_reason,
            )
        if self.runtime is not None:
            health = self.runtime.is_available()
            return ConnectorHealthResult(
                name=self.name,
                enabled=True,
                available=health.available,
                source_kind=self.source_kind,
                reason=health.reason,
            )
        return ConnectorHealthResult(
            name=self.name,
            enabled=True,
            available=True,
            source_kind=self.source_kind,
            reason="configured",
        )


@dataclass(frozen=True)
class _TokenConnectorHealthProvider:
    name: str
    source_kind: KnowledgeSourceKind
    enabled: bool
    token_env: str
    has_required_metadata: bool
    missing_metadata_reason: str

    def check_health(self, env: dict[str, str] | None = None) -> ConnectorHealthResult:
        if not self.enabled:
            return ConnectorHealthResult(
                name=self.name,
                enabled=False,
                available=False,
                source_kind=self.source_kind,
                reason="disabled",
            )
        if not self.has_required_metadata:
            return ConnectorHealthResult(
                name=self.name,
                enabled=True,
                available=False,
                source_kind=self.source_kind,
                reason=self.missing_metadata_reason,
            )
        source = env if env is not None else os.environ
        if not source.get(self.token_env):
            return ConnectorHealthResult(
                name=self.name,
                enabled=True,
                available=False,
                source_kind=self.source_kind,
                reason=f"{self.token_env} is not set",
            )
        return ConnectorHealthResult(
            name=self.name,
            enabled=True,
            available=True,
            source_kind=self.source_kind,
            reason="configured",
        )
