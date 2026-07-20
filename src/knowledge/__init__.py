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
from knowledge.registry import (
    ConnectorHealthResult,
    KnowledgeConnectorRegistry,
    build_connector_registry,
)

__all__ = [
    "ConnectorHealthResult",
    "KnowledgeConnector",
    "KnowledgeConnectorHealth",
    "KnowledgeConnectorRegistry",
    "KnowledgeConnectorRequest",
    "KnowledgeItem",
    "KnowledgeSourceKind",
    "build_connector_registry",
    "normalize_knowledge_items",
    "sanitize_knowledge_text",
]
