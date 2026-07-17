"""Optional knowledge connector contracts for OpenRabbit."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Protocol, runtime_checkable

SECRET_REDACTION = "[REDACTED]"
MAX_KNOWLEDGE_TEXT_CHARS = 1200

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b("
        r"github_pat_[A-Za-z0-9_]+|"
        r"ghp_[A-Za-z0-9_]+|"
        r"gho_[A-Za-z0-9_]+|"
        r"sk-[A-Za-z0-9_-]{20,}|"
        r"xox[baprs]-[A-Za-z0-9-]+"
        r")\b"
    ),
    re.compile(
        r"(?i)\b("
        r"(?:api[_-]?key|token|secret|password|authorization)"
        r"\s*[:=]\s*)"
        r"([^\s,;]{8,})"
    ),
)


class KnowledgeSourceKind(str, Enum):
    """Supported optional knowledge source categories."""

    MCP = "mcp"
    WEB_SEARCH = "web_search"
    MULTI_REPO = "multi_repo"
    ISSUE_TRACKER = "issue_tracker"
    DOCUMENT = "document"


@dataclass(frozen=True)
class KnowledgeConnectorRequest:
    """One bounded request for optional knowledge context."""

    repo: str
    pr_number: int
    head_sha: str = ""
    changed_paths: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    query: str = ""
    max_items: int = 8
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.repo.strip():
            raise ValueError("repo is required")
        if self.pr_number <= 0:
            raise ValueError("pr_number must be positive")
        if self.max_items <= 0 or self.max_items > 50:
            raise ValueError("max_items must be between 1 and 50")


@dataclass(frozen=True)
class KnowledgeItem:
    """One sanitized knowledge item returned by a connector."""

    source_id: str
    source_kind: KnowledgeSourceKind
    title: str
    body: str
    url: str = ""
    repo: str = ""
    path: str = ""
    score: float | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeConnectorHealth:
    """Non-fatal availability state for one optional connector."""

    name: str
    source_kind: KnowledgeSourceKind
    available: bool
    reason: str = ""


@runtime_checkable
class KnowledgeConnector(Protocol):
    """Optional provider of extra review knowledge.

    Connectors must be read-only from OpenRabbit's perspective. They return
    untrusted context snippets and never publish comments or mutate pull requests.
    """

    name: str
    source_kind: KnowledgeSourceKind

    def is_available(self) -> KnowledgeConnectorHealth:
        """Return non-fatal connector health."""

    def retrieve(self, request: KnowledgeConnectorRequest) -> Sequence[KnowledgeItem]:
        """Return bounded knowledge items for one pull request."""


def sanitize_knowledge_text(value: object, max_chars: int = MAX_KNOWLEDGE_TEXT_CHARS) -> str:
    """Redact common secrets and bound text before prompt use."""
    if not isinstance(value, str) or max_chars <= 0:
        return ""
    body = value.strip()
    for pattern in _SECRET_PATTERNS:
        body = pattern.sub(_redact_secret_match, body)
    body = " ".join(body.split())
    if len(body) <= max_chars:
        return body
    return f"{body[: max_chars - 3].rstrip()}..."


def normalize_knowledge_items(
    items: Iterable[KnowledgeItem],
    *,
    max_items: int = 8,
    max_body_chars: int = MAX_KNOWLEDGE_TEXT_CHARS,
) -> list[KnowledgeItem]:
    """Return deterministic, prompt-safe connector items."""
    if max_items <= 0:
        return []

    sanitized: list[KnowledgeItem] = []
    for item in items:
        title = sanitize_knowledge_text(item.title, max_chars=180)
        body = sanitize_knowledge_text(item.body, max_chars=max_body_chars)
        if not title and not body:
            continue
        metadata = {
            key_text: value_text
            for key, value in item.metadata.items()
            if (key_text := sanitize_knowledge_text(key, max_chars=80))
            and (value_text := sanitize_knowledge_text(value, max_chars=300))
        }
        sanitized.append(
            KnowledgeItem(
                source_id=sanitize_knowledge_text(item.source_id, max_chars=180),
                source_kind=item.source_kind,
                title=title,
                body=body,
                url=sanitize_knowledge_text(item.url, max_chars=300),
                repo=sanitize_knowledge_text(item.repo, max_chars=120),
                path=sanitize_knowledge_text(item.path, max_chars=300),
                score=_normalize_score(item.score),
                metadata=metadata,
            )
        )

    return sorted(
        sanitized,
        key=lambda item: (-(item.score or 0.0), item.source_kind.value, item.source_id),
    )[:max_items]


def _redact_secret_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 2:
        return f"{match.group(1)}{SECRET_REDACTION}"
    return SECRET_REDACTION


def _normalize_score(score: float | None) -> float | None:
    if score is None or not isfinite(score):
        return None
    return min(max(score, 0.0), 1.0)
