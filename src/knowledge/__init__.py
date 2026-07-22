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
from knowledge.jira import (
    MANAGED_COMMENT_MARKER,
    JiraClientError,
    JiraCommentPublishResult,
    JiraConnector,
    JiraRestClient,
    extract_jira_issue_keys,
)
from knowledge.linear import (
    LINEAR_GRAPHQL_ENDPOINT,
    MANAGED_LINEAR_COMMENT_MARKER,
    LinearClientError,
    LinearCommentPublishResult,
    LinearConnector,
    LinearGraphqlClient,
    extract_linear_issue_ids,
)
from knowledge.mcp_runtime import McpConnectorRuntime, McpServerHealth, mcp_sdk_available
from knowledge.registry import (
    ConnectorHealthResult,
    KnowledgeConnectorRegistry,
    build_connector_registry,
)
from knowledge.web_search import McpWebSearchConnector

__all__ = [
    "LINEAR_GRAPHQL_ENDPOINT",
    "MANAGED_COMMENT_MARKER",
    "MANAGED_LINEAR_COMMENT_MARKER",
    "ConnectorHealthResult",
    "JiraClientError",
    "JiraCommentPublishResult",
    "JiraConnector",
    "JiraRestClient",
    "KnowledgeConnector",
    "KnowledgeConnectorHealth",
    "KnowledgeConnectorRegistry",
    "KnowledgeConnectorRequest",
    "KnowledgeItem",
    "KnowledgeSourceKind",
    "LinearClientError",
    "LinearCommentPublishResult",
    "LinearConnector",
    "LinearGraphqlClient",
    "McpConnectorRuntime",
    "McpServerHealth",
    "McpWebSearchConnector",
    "build_connector_registry",
    "extract_jira_issue_keys",
    "extract_linear_issue_ids",
    "mcp_sdk_available",
    "normalize_knowledge_items",
    "sanitize_knowledge_text",
]
