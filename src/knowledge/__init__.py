"""Optional knowledge connector contracts."""

from knowledge.connectors import (
    KnowledgeConnector,
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)
from knowledge.mcp_runtime import McpConnectorRuntime, McpServerHealth, mcp_sdk_available
from knowledge.registry import (
    ConnectorHealthResult,
    KnowledgeConnectorRegistry,
    build_connector_registry,
)
from knowledge.web_search import McpWebSearchConnector

__all__ = [
    "ConnectorHealthResult",
    "KnowledgeConnector",
    "KnowledgeConnectorHealth",
    "KnowledgeConnectorRegistry",
    "KnowledgeConnectorRequest",
    "KnowledgeItem",
    "KnowledgeSourceKind",
    "McpConnectorRuntime",
    "McpServerHealth",
    "McpWebSearchConnector",
    "build_connector_registry",
    "mcp_sdk_available",
    "normalize_knowledge_items",
    "sanitize_knowledge_text",
]
