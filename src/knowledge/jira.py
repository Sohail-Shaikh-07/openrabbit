"""Jira issue tracker knowledge connector."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

import httpx

from configs.schema import JiraConnectorSettings
from knowledge.connectors import (
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)

MANAGED_COMMENT_MARKER = "<!-- openrabbit:jira-managed-comment -->"
_JIRA_KEY_PATTERN = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9])")
_MAX_DESCRIPTION_CHARS = 900
_MAX_COMMENT_CHARS = 6000


class JiraClientError(RuntimeError):
    """Raised when Jira returns an unavailable or invalid response."""


class JiraClient(Protocol):
    """Minimal Jira API client used by the connector."""

    def fetch_issue(self, key: str) -> Mapping[str, object]:
        """Fetch one Jira issue by key."""

    def list_comments(self, key: str) -> Sequence[Mapping[str, object]]:
        """Return comments for one Jira issue."""

    def create_comment(self, key: str, body: str) -> str:
        """Create one Jira comment and return its id."""

    def update_comment(self, key: str, comment_id: str, body: str) -> str:
        """Update one Jira comment and return its id."""


@dataclass(frozen=True)
class JiraCommentPublishResult:
    """Result of an optional managed Jira comment write."""

    issue_key: str
    action: str
    comment_id: str = ""
    reason: str = ""


class JiraRestClient:
    """Small REST client for Jira Cloud-compatible APIs."""

    def __init__(self, *, base_url: str, token: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "Authorization": _authorization_header(token),
            "Content-Type": "application/json",
        }
        self._timeout_seconds = timeout_seconds

    def fetch_issue(self, key: str) -> Mapping[str, object]:
        return self._request_json(
            "GET",
            f"/rest/api/3/issue/{key}",
            params={"fields": "summary,status,labels,description"},
        )

    def list_comments(self, key: str) -> Sequence[Mapping[str, object]]:
        payload = self._request_json("GET", f"/rest/api/3/issue/{key}/comment")
        comments = payload.get("comments")
        if not isinstance(comments, Sequence) or isinstance(comments, str | bytes):
            return []
        return [comment for comment in comments if isinstance(comment, Mapping)]

    def create_comment(self, key: str, body: str) -> str:
        payload = self._request_json(
            "POST",
            f"/rest/api/3/issue/{key}/comment",
            json={"body": _adf_from_text(body)},
        )
        return _object_text(payload, "id")

    def update_comment(self, key: str, comment_id: str, body: str) -> str:
        payload = self._request_json(
            "PUT",
            f"/rest/api/3/issue/{key}/comment/{comment_id}",
            json={"body": _adf_from_text(body)},
        )
        return _object_text(payload, "id") or comment_id

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        url = f"{self._base_url}{path}"
        try:
            response = httpx.request(
                method,
                url,
                headers=self._headers,
                params=params,
                json=json,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise JiraClientError(str(exc)) from exc
        except ValueError as exc:
            raise JiraClientError("Jira returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise JiraClientError("Jira returned an unexpected response")
        return cast(Mapping[str, object], payload)


class JiraConnector:
    """Optional Jira connector for linked issue context and managed comments."""

    name = "jira"
    source_kind = KnowledgeSourceKind.ISSUE_TRACKER

    def __init__(
        self,
        settings: JiraConnectorSettings,
        *,
        token: str | None = None,
        client: JiraClient | None = None,
    ) -> None:
        self._settings = settings
        self._token = token
        self._client = client

    def is_available(self) -> KnowledgeConnectorHealth:
        """Return local Jira connector availability without contacting Jira."""
        if not self._settings.enabled:
            return _health(False, "disabled")
        if not self._settings.base_url:
            return _health(False, "no Jira base_url configured")
        if not self._get_token():
            return _health(False, f"{self._settings.token_env} is not set")
        return _health(True, "configured")

    def retrieve(self, request: KnowledgeConnectorRequest) -> Sequence[KnowledgeItem]:
        """Return bounded Jira issue context, or an empty list on failure."""
        health = self.is_available()
        if not health.available:
            return []

        keys = extract_jira_issue_keys(*_request_texts(request))
        if not keys:
            return []

        max_items = min(request.max_items, self._settings.max_items, len(keys))
        client = self._get_client()
        items: list[KnowledgeItem] = []
        for key in keys[:max_items]:
            try:
                items.append(_issue_to_item(client.fetch_issue(key), base_url=self._base_url()))
            except Exception:
                continue
        return normalize_knowledge_items(items, max_items=max_items)

    def publish_managed_comment(self, issue_key: str, body: str) -> JiraCommentPublishResult:
        """Create or update one managed OpenRabbit comment on a Jira issue."""
        key = _normalize_issue_key(issue_key)
        if not key:
            return JiraCommentPublishResult(
                issue_key=issue_key, action="skipped", reason="invalid key"
            )
        if not self._settings.enabled:
            return JiraCommentPublishResult(issue_key=key, action="skipped", reason="disabled")
        if not self._settings.write_enabled:
            return JiraCommentPublishResult(
                issue_key=key,
                action="skipped",
                reason="write mode disabled",
            )
        if not self._settings.managed_comments:
            return JiraCommentPublishResult(
                issue_key=key,
                action="skipped",
                reason="managed comments disabled",
            )

        health = self.is_available()
        if not health.available:
            return JiraCommentPublishResult(issue_key=key, action="skipped", reason=health.reason)

        comment_body = _managed_comment_body(body)
        try:
            client = self._get_client()
            existing_id = _find_managed_comment_id(client.list_comments(key))
            if existing_id:
                comment_id = client.update_comment(key, existing_id, comment_body)
                return JiraCommentPublishResult(
                    issue_key=key,
                    action="updated",
                    comment_id=comment_id,
                )
            comment_id = client.create_comment(key, comment_body)
            return JiraCommentPublishResult(issue_key=key, action="created", comment_id=comment_id)
        except Exception as exc:
            return JiraCommentPublishResult(
                issue_key=key,
                action="failed",
                reason=sanitize_knowledge_text(str(exc), max_chars=180),
            )

    def _base_url(self) -> str:
        return self._settings.base_url or ""

    def _get_token(self) -> str:
        if self._token is not None:
            return self._token
        return os.environ.get(self._settings.token_env, "")

    def _get_client(self) -> JiraClient:
        if self._client is not None:
            return self._client
        return JiraRestClient(base_url=self._base_url(), token=self._get_token())


def extract_jira_issue_keys(*texts: str) -> tuple[str, ...]:
    """Extract deterministic, deduplicated Jira issue keys from text."""
    keys: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _JIRA_KEY_PATTERN.finditer(text.upper()):
            key = match.group(1)
            if key not in seen:
                keys.append(key)
                seen.add(key)
    return tuple(keys)


def _health(available: bool, reason: str) -> KnowledgeConnectorHealth:
    return KnowledgeConnectorHealth(
        name="jira",
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


def _normalize_issue_key(value: str) -> str:
    keys = extract_jira_issue_keys(value)
    return keys[0] if keys else ""


def _issue_to_item(issue: Mapping[str, object], *, base_url: str) -> KnowledgeItem:
    key = _object_text(issue, "key")
    fields = issue.get("fields")
    field_map = fields if isinstance(fields, Mapping) else {}
    summary = _object_text(field_map, "summary")
    status = _status_name(field_map.get("status"))
    labels = _string_list(field_map.get("labels"))
    description = sanitize_knowledge_text(
        _extract_jira_text(field_map.get("description")),
        max_chars=_MAX_DESCRIPTION_CHARS,
    )

    body_parts = [
        f"Status: {status}" if status else "",
        f"Labels: {', '.join(labels)}" if labels else "",
        f"Description: {description}" if description else "",
    ]
    body = "\n".join(part for part in body_parts if part)
    url = f"{base_url}/browse/{key}" if base_url and key else ""
    return KnowledgeItem(
        source_id=f"jira:{key}",
        source_kind=KnowledgeSourceKind.ISSUE_TRACKER,
        title=f"{key}: {summary}" if summary else key,
        body=body,
        url=url,
        score=1.0,
        metadata={
            "provider": "jira",
            "key": key,
            "status": status,
            "labels": ", ".join(labels),
            "trust": "untrusted",
        },
    )


def _find_managed_comment_id(comments: Sequence[Mapping[str, object]]) -> str:
    for comment in comments:
        body = _extract_jira_text(comment.get("body"))
        if MANAGED_COMMENT_MARKER in body:
            return _object_text(comment, "id")
    return ""


def _managed_comment_body(body: str) -> str:
    sanitized = sanitize_knowledge_text(body, max_chars=_MAX_COMMENT_CHARS)
    if MANAGED_COMMENT_MARKER in sanitized:
        return sanitized
    return f"{MANAGED_COMMENT_MARKER}\n{sanitized}".strip()


def _authorization_header(token: str) -> str:
    stripped = token.strip()
    if stripped.lower().startswith(("bearer ", "basic ")):
        return stripped
    return f"Bearer {stripped}"


def _adf_from_text(body: str) -> Mapping[str, object]:
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line or " "}]}
        for line in body.splitlines()
    ]
    return {"type": "doc", "version": 1, "content": paragraphs or []}


def _extract_jira_text(value: object) -> str:
    if isinstance(value, str):
        return value
    parts: list[str] = []
    _collect_jira_text(value, parts)
    return " ".join(part for part in parts if part)


def _collect_jira_text(value: object, parts: list[str]) -> None:
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            parts.append(text)
        for child_key in ("content", "paragraphs", "items"):
            _collect_jira_text(value.get(child_key), parts)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for item in value:
            _collect_jira_text(item, parts)


def _status_name(value: object) -> str:
    if isinstance(value, Mapping):
        return _object_text(value, "name")
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str)]


def _object_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    return item.strip() if isinstance(item, str) else ""
