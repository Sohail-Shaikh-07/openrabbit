"""Structured pull request history for prompt and re-review workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from memory.models import LearningMemoryRecord, PullRequestMemoryHistory

MAX_CONVERSATION_BODY_CHARS = 1200
SECRET_REDACTION = "[REDACTED]"

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


@dataclass(frozen=True)
class ConversationEvent:
    """One human or bot-authored event from a PR conversation."""

    source: str
    author: str
    body: str
    url: str
    created_at: datetime | None = None
    file: str = ""
    line: int | None = None
    state: str = ""
    commit_id: str = ""


@dataclass(frozen=True)
class PullRequestHistory:
    """One structured view of local memory plus PR conversation context."""

    repo: str
    pr_number: int
    head_sha: str
    commit_shas: list[str] = field(default_factory=list)
    local: PullRequestMemoryHistory | None = None
    conversation: list[ConversationEvent] = field(default_factory=list)
    learnings: list[LearningMemoryRecord] = field(default_factory=list)

    @classmethod
    def from_payload(
        cls,
        *,
        repo: str,
        payload: Any,
        local: PullRequestMemoryHistory | None = None,
        conversation: list[ConversationEvent] | None = None,
        learnings: list[LearningMemoryRecord] | None = None,
    ) -> PullRequestHistory:
        commits = getattr(payload, "commits", None)
        commit_shas = (
            [
                str(getattr(commit, "sha", ""))
                for commit in commits
                if str(getattr(commit, "sha", ""))
            ]
            if isinstance(commits, list)
            else []
        )
        return cls(
            repo=repo,
            pr_number=int(getattr(payload, "number", 0) or 0),
            head_sha=str(getattr(payload, "head_sha", "") or ""),
            commit_shas=commit_shas,
            local=local,
            conversation=list(conversation or []),
            learnings=list(learnings or []),
        )


def format_history_context(history: Any | None, *, max_events: int = 8) -> str:
    """Return compact prompt-ready history context."""
    if history is None:
        return "(No PR history loaded.)"

    lines = ["PR history:"]
    if history.local is not None and history.local.last_reviewed_sha:
        lines.append(f"- Last reviewed SHA: {history.local.last_reviewed_sha}")
    if history.commit_shas:
        lines.append(f"- Commits seen: {', '.join(history.commit_shas[-5:])}")

    previous = history.local.previous_findings if history.local is not None else []
    for record in previous[:5]:
        location = record.file
        if record.line > 0:
            location = f"{location}:{record.line}"
        lines.append(
            f"- Previous finding [{record.status.value}] {record.title} "
            f"({location}, first seen {record.first_seen_sha[:12]})"
        )

    raw_learnings = getattr(history, "learnings", [])
    active_learnings = [
        learning for learning in raw_learnings if getattr(learning, "active", False)
    ]
    if active_learnings:
        lines.append("Active repository learnings:")
    for learning in active_learnings[:8]:
        source = ""
        if learning.source_pr_number is not None:
            source = f" (from PR #{learning.source_pr_number})"
        lines.append(f"- Learning{source}: {learning.instruction}")

    for event in history.conversation[:max_events]:
        body = " ".join(event.body.split())
        if len(body) > 220:
            body = f"{body[:217]}..."
        location = f" {event.file}:{event.line}" if event.file and event.line else ""
        lines.append(f"- {event.source}{location} by {event.author}: {body}")

    if len(lines) == 1:
        return "(No PR history loaded.)"
    return "\n".join(lines)


def conversation_events_from_github(
    *,
    reviews: list[Any],
    review_comments: list[Any],
    issue_comments: list[Any],
) -> list[ConversationEvent]:
    """Normalize GitHub review and comment objects into conversation events."""
    events: list[ConversationEvent] = []
    for review in reviews:
        body = sanitize_conversation_body(getattr(review, "body", ""))
        if not body:
            continue
        events.append(
            ConversationEvent(
                source="review",
                author=str(getattr(getattr(review, "user", None), "login", "")),
                body=body,
                url=str(getattr(review, "html_url", "")),
                created_at=getattr(review, "submitted_at", None),
                state=str(getattr(review, "state", "")),
                commit_id=str(getattr(review, "commit_id", "") or ""),
            )
        )

    for comment in review_comments:
        body = sanitize_conversation_body(getattr(comment, "body", ""))
        if not body:
            continue
        events.append(
            ConversationEvent(
                source="review_comment",
                author=str(getattr(getattr(comment, "user", None), "login", "")),
                body=body,
                url=str(getattr(comment, "html_url", "")),
                created_at=getattr(comment, "created_at", None),
                file=str(getattr(comment, "path", "")),
                line=getattr(comment, "line", None),
                commit_id=str(getattr(comment, "commit_id", "") or ""),
            )
        )

    for comment in issue_comments:
        body = sanitize_conversation_body(getattr(comment, "body", ""))
        if not body:
            continue
        events.append(
            ConversationEvent(
                source="issue_comment",
                author=str(getattr(getattr(comment, "user", None), "login", "")),
                body=body,
                url=str(getattr(comment, "html_url", "")),
                created_at=getattr(comment, "created_at", None),
            )
        )

    return sorted(events, key=lambda event: event.created_at or datetime.min)


def sanitize_conversation_body(value: object) -> str:
    """Return prompt-safe PR conversation text.

    PR comments can include pasted tokens, logs, or huge generated output. History
    ingestion keeps enough context for re-review while redacting common secrets
    and bounding the text before it reaches prompts.
    """
    if not isinstance(value, str):
        return ""

    body = value.strip()
    for pattern in _SECRET_PATTERNS:
        body = pattern.sub(_redact_secret_match, body)
    body = " ".join(body.split())
    if len(body) <= MAX_CONVERSATION_BODY_CHARS:
        return body
    return f"{body[: MAX_CONVERSATION_BODY_CHARS - 3].rstrip()}..."


def _redact_secret_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 2:
        return f"{match.group(1)}{SECRET_REDACTION}"
    return SECRET_REDACTION
