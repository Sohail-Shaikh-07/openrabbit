"""Linear issue tracker knowledge connector."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

import httpx

from configs.schema import LinearConnectorSettings
from knowledge.connectors import (
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)

LINEAR_GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"
MANAGED_LINEAR_COMMENT_MARKER = "<!-- openrabbit:linear-managed-comment -->"
_LINEAR_IDENTIFIER_PATTERN = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9])")
_MAX_DESCRIPTION_CHARS = 900
_MAX_COMMENT_CHARS = 6000

_ISSUE_QUERY = """
query OpenRabbitIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    url
    state { name }
    labels { nodes { name } }
  }
}
"""

_COMMENTS_QUERY = """
query OpenRabbitIssueComments($id: String!) {
  issue(id: $id) {
    comments { nodes { id body } }
  }
}
"""

_COMMENT_CREATE_MUTATION = """
mutation OpenRabbitCommentCreate($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment { id }
  }
}
"""

_COMMENT_UPDATE_MUTATION = """
mutation OpenRabbitCommentUpdate($id: String!, $input: CommentUpdateInput!) {
  commentUpdate(id: $id, input: $input) {
    success
    comment { id }
  }
}
"""


class LinearClientError(RuntimeError):
    """Raised when Linear returns an unavailable or invalid response."""


class LinearClient(Protocol):
    """Minimal Linear API client used by the connector."""

    def fetch_issue(self, identifier: str) -> Mapping[str, object]:
        """Fetch one Linear issue by identifier or id."""

    def list_comments(self, issue_id: str) -> Sequence[Mapping[str, object]]:
        """Return comments for one Linear issue id."""

    def create_comment(self, issue_id: str, body: str) -> str:
        """Create one Linear comment and return its id."""

    def update_comment(self, comment_id: str, body: str) -> str:
        """Update one Linear comment and return its id."""


@dataclass(frozen=True)
class LinearCommentPublishResult:
    """Result of an optional managed Linear comment write."""

    issue_identifier: str
    action: str
    comment_id: str = ""
    reason: str = ""


class LinearGraphqlClient:
    """Small GraphQL client for Linear APIs."""

    def __init__(self, *, endpoint: str = LINEAR_GRAPHQL_ENDPOINT, token: str) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "Authorization": token.strip(),
            "Content-Type": "application/json",
        }

    def fetch_issue(self, identifier: str) -> Mapping[str, object]:
        data = self._execute(_ISSUE_QUERY, {"id": identifier})
        issue = data.get("issue")
        if not isinstance(issue, Mapping):
            raise LinearClientError("Linear issue was not found")
        return cast(Mapping[str, object], issue)

    def list_comments(self, issue_id: str) -> Sequence[Mapping[str, object]]:
        data = self._execute(_COMMENTS_QUERY, {"id": issue_id})
        issue = data.get("issue")
        if not isinstance(issue, Mapping):
            return []
        comments = issue.get("comments")
        if not isinstance(comments, Mapping):
            return []
        nodes = comments.get("nodes")
        if not isinstance(nodes, Sequence) or isinstance(nodes, str | bytes):
            return []
        return [comment for comment in nodes if isinstance(comment, Mapping)]

    def create_comment(self, issue_id: str, body: str) -> str:
        data = self._execute(
            _COMMENT_CREATE_MUTATION,
            {"input": {"issueId": issue_id, "body": body}},
        )
        payload = data.get("commentCreate")
        if not isinstance(payload, Mapping) or payload.get("success") is not True:
            raise LinearClientError("Linear comment create failed")
        comment = payload.get("comment")
        return _object_text(comment if isinstance(comment, Mapping) else {}, "id")

    def update_comment(self, comment_id: str, body: str) -> str:
        data = self._execute(
            _COMMENT_UPDATE_MUTATION,
            {"id": comment_id, "input": {"body": body}},
        )
        payload = data.get("commentUpdate")
        if not isinstance(payload, Mapping) or payload.get("success") is not True:
            raise LinearClientError("Linear comment update failed")
        comment = payload.get("comment")
        return _object_text(comment if isinstance(comment, Mapping) else {}, "id") or comment_id

    def _execute(self, query: str, variables: Mapping[str, object]) -> Mapping[str, object]:
        try:
            response = httpx.post(
                self._endpoint,
                headers=self._headers,
                json={"query": query, "variables": variables},
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise LinearClientError(str(exc)) from exc
        except ValueError as exc:
            raise LinearClientError("Linear returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise LinearClientError("Linear returned an unexpected response")
        errors = payload.get("errors")
        if errors:
            raise LinearClientError("Linear returned GraphQL errors")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise LinearClientError("Linear returned no data")
        return cast(Mapping[str, object], data)


class LinearConnector:
    """Optional Linear connector for linked issue context and managed comments."""

    name = "linear"
    source_kind = KnowledgeSourceKind.ISSUE_TRACKER

    def __init__(
        self,
        settings: LinearConnectorSettings,
        *,
        token: str | None = None,
        client: LinearClient | None = None,
    ) -> None:
        self._settings = settings
        self._token = token
        self._client = client

    def is_available(self) -> KnowledgeConnectorHealth:
        """Return local Linear connector availability without contacting Linear."""
        if not self._settings.enabled:
            return _health(False, "disabled")
        if not self._get_token():
            return _health(False, f"{self._settings.token_env} is not set")
        return _health(True, "configured")

    def retrieve(self, request: KnowledgeConnectorRequest) -> Sequence[KnowledgeItem]:
        """Return bounded Linear issue context, or an empty list on failure."""
        health = self.is_available()
        if not health.available:
            return []

        identifiers = extract_linear_issue_ids(*_request_texts(request))
        if not identifiers:
            return []

        max_items = min(request.max_items, self._settings.max_items, len(identifiers))
        client = self._get_client()
        items: list[KnowledgeItem] = []
        for identifier in identifiers[:max_items]:
            try:
                items.append(_issue_to_item(client.fetch_issue(identifier), fallback=identifier))
            except Exception:
                continue
        return normalize_knowledge_items(items, max_items=max_items)

    def publish_managed_comment(
        self, issue_identifier: str, body: str
    ) -> LinearCommentPublishResult:
        """Create or update one managed OpenRabbit comment on a Linear issue."""
        identifier = _normalize_issue_identifier(issue_identifier)
        if not identifier:
            return LinearCommentPublishResult(
                issue_identifier=issue_identifier,
                action="skipped",
                reason="invalid identifier",
            )
        if not self._settings.enabled:
            return LinearCommentPublishResult(
                issue_identifier=identifier,
                action="skipped",
                reason="disabled",
            )
        if not self._settings.write_enabled:
            return LinearCommentPublishResult(
                issue_identifier=identifier,
                action="skipped",
                reason="write mode disabled",
            )
        if not self._settings.managed_comments:
            return LinearCommentPublishResult(
                issue_identifier=identifier,
                action="skipped",
                reason="managed comments disabled",
            )

        health = self.is_available()
        if not health.available:
            return LinearCommentPublishResult(
                issue_identifier=identifier,
                action="skipped",
                reason=health.reason,
            )

        comment_body = _managed_comment_body(body)
        try:
            client = self._get_client()
            issue = client.fetch_issue(identifier)
            issue_id = _object_text(issue, "id")
            if not issue_id:
                return LinearCommentPublishResult(
                    issue_identifier=identifier,
                    action="failed",
                    reason="Linear issue id is missing",
                )
            existing_id = _find_managed_comment_id(client.list_comments(issue_id))
            if existing_id:
                comment_id = client.update_comment(existing_id, comment_body)
                return LinearCommentPublishResult(
                    issue_identifier=identifier,
                    action="updated",
                    comment_id=comment_id,
                )
            comment_id = client.create_comment(issue_id, comment_body)
            return LinearCommentPublishResult(
                issue_identifier=identifier,
                action="created",
                comment_id=comment_id,
            )
        except Exception as exc:
            return LinearCommentPublishResult(
                issue_identifier=identifier,
                action="failed",
                reason=sanitize_knowledge_text(str(exc), max_chars=180),
            )

    def _endpoint(self) -> str:
        return self._settings.base_url or LINEAR_GRAPHQL_ENDPOINT

    def _get_token(self) -> str:
        if self._token is not None:
            return self._token
        return os.environ.get(self._settings.token_env, "")

    def _get_client(self) -> LinearClient:
        if self._client is not None:
            return self._client
        return LinearGraphqlClient(endpoint=self._endpoint(), token=self._get_token())


def extract_linear_issue_ids(*texts: str) -> tuple[str, ...]:
    """Extract deterministic, deduplicated Linear issue identifiers from text."""
    identifiers: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _LINEAR_IDENTIFIER_PATTERN.finditer(text.upper()):
            identifier = match.group(1)
            if identifier not in seen:
                identifiers.append(identifier)
                seen.add(identifier)
    return tuple(identifiers)


def _health(available: bool, reason: str) -> KnowledgeConnectorHealth:
    return KnowledgeConnectorHealth(
        name="linear",
        source_kind=KnowledgeSourceKind.ISSUE_TRACKER,
        available=available,
        reason=reason,
    )


def _request_texts(request: KnowledgeConnectorRequest) -> tuple[str, ...]:
    metadata_text = " ".join(
        value for key, value in request.metadata.items() if key and isinstance(value, str)
    )
    return (
        request.query,
        metadata_text,
        " ".join(request.changed_paths),
        " ".join(request.changed_symbols),
    )


def _normalize_issue_identifier(value: str) -> str:
    identifiers = extract_linear_issue_ids(value)
    return identifiers[0] if identifiers else ""


def _issue_to_item(issue: Mapping[str, object], *, fallback: str) -> KnowledgeItem:
    issue_id = _object_text(issue, "id")
    identifier = _object_text(issue, "identifier") or fallback
    title = _object_text(issue, "title")
    state = _state_name(issue.get("state"))
    labels = _label_names(issue.get("labels"))
    url = _object_text(issue, "url")
    description = sanitize_knowledge_text(
        _object_text(issue, "description"),
        max_chars=_MAX_DESCRIPTION_CHARS,
    )

    body_parts = [
        f"State: {state}" if state else "",
        f"Labels: {', '.join(labels)}" if labels else "",
        f"Description: {description}" if description else "",
    ]
    body = "\n".join(part for part in body_parts if part)
    return KnowledgeItem(
        source_id=f"linear:{identifier}",
        source_kind=KnowledgeSourceKind.ISSUE_TRACKER,
        title=f"{identifier}: {title}" if title else identifier,
        body=body,
        url=url,
        score=1.0,
        metadata={
            "provider": "linear",
            "id": issue_id,
            "identifier": identifier,
            "state": state,
            "labels": ", ".join(labels),
            "trust": "untrusted",
        },
    )


def _find_managed_comment_id(comments: Sequence[Mapping[str, object]]) -> str:
    for comment in comments:
        body = _object_text(comment, "body")
        if MANAGED_LINEAR_COMMENT_MARKER in body:
            return _object_text(comment, "id")
    return ""


def _managed_comment_body(body: str) -> str:
    sanitized = sanitize_knowledge_text(body, max_chars=_MAX_COMMENT_CHARS)
    if MANAGED_LINEAR_COMMENT_MARKER in sanitized:
        return sanitized
    return f"{MANAGED_LINEAR_COMMENT_MARKER}\n{sanitized}".strip()


def _state_name(value: object) -> str:
    if isinstance(value, Mapping):
        return _object_text(value, "name")
    return value if isinstance(value, str) else ""


def _label_names(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    nodes = value.get("nodes")
    if not isinstance(nodes, Sequence) or isinstance(nodes, str | bytes):
        return []
    labels: list[str] = []
    for node in nodes:
        if isinstance(node, Mapping):
            label = _object_text(node, "name")
            if label:
                labels.append(label)
    return labels


def _object_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    return item.strip() if isinstance(item, str) else ""
