"""Shared PR history loading for model-facing CLI commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from cli.logging import get_logger
from configs.settings import Settings
from github_.repository import RepositoryHandle
from memory.backends import PullRequestMemoryBackend
from memory.history import PullRequestHistory, conversation_events_from_github
from memory.store import SQLitePullRequestMemory

_log = get_logger(__name__)


@dataclass(frozen=True)
class HistoryLoadResult:
    """Best-effort history loading result."""

    history: PullRequestHistory | None
    store: PullRequestMemoryBackend | None = None
    error: str | None = None

    @property
    def conversation_count(self) -> int:
        if self.history is None:
            return 0
        return len(self.history.conversation)

    @property
    def learning_count(self) -> int:
        if self.history is None:
            return 0
        return len(self.history.learnings)


async def load_pr_history(
    settings: Settings,
    *,
    handle: RepositoryHandle,
    payload: Any,
    memory_store: PullRequestMemoryBackend | None = None,
    include_conversation: bool = True,
) -> HistoryLoadResult:
    """Load local memory, learnings, and GitHub PR conversation context.

    The memory feature gate controls history loading. Conversation fetches are
    intentionally best effort: OpenRabbit should still review from local memory
    and the diff when GitHub comments are temporarily unavailable.
    """
    if not settings.memory.enabled:
        return HistoryLoadResult(history=None)

    store: PullRequestMemoryBackend | None = None
    try:
        store = memory_store or SQLitePullRequestMemory(settings.resolved_memory_path())
        local = store.load_history(handle.full_name, payload.number)
        learnings = _load_repo_learnings(settings, store, handle.full_name)
    except Exception as exc:
        _log.warning(
            "history.local_load_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return HistoryLoadResult(history=None, store=store, error=type(exc).__name__)

    conversation = []
    if include_conversation:
        try:
            conversation = await _load_conversation(handle, payload.number)
        except Exception as exc:
            _log.warning(
                "history.conversation_load_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    return HistoryLoadResult(
        history=PullRequestHistory.from_payload(
            repo=handle.full_name,
            payload=payload,
            local=local,
            conversation=conversation,
            learnings=learnings,
        ),
        store=store,
    )


async def _load_conversation(handle: RepositoryHandle, pr_number: int) -> list[Any]:
    reviews_result, review_comments_result, issue_comments_result = await asyncio.gather(
        handle.list_pull_reviews(pr_number),
        handle.list_pull_review_comments(pr_number),
        handle.list_issue_comments(pr_number),
        return_exceptions=True,
    )

    reviews = _list_or_raise(reviews_result)
    review_comments = _list_or_raise(review_comments_result)
    issue_comments = _list_or_raise(issue_comments_result)
    return conversation_events_from_github(
        reviews=reviews,
        review_comments=review_comments,
        issue_comments=issue_comments,
    )


def _list_or_raise(value: object) -> list[Any]:
    if isinstance(value, BaseException):
        raise value
    return value if isinstance(value, list) else []


def _load_repo_learnings(
    settings: Settings,
    store: PullRequestMemoryBackend,
    repo: str,
) -> list[Any]:
    if not settings.memory.learnings_enabled:
        return []
    if isinstance(store, SQLitePullRequestMemory):
        return store.list_learnings(repo)
    return []
