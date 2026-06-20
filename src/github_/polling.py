"""Polling service.

The service watches one GitHub repository on a fixed interval. Each round it
lists open pull requests, diffs them against the last persisted state, and
fires one of three events per change:

- ``pull_request_opened`` for a number we have never seen.
- ``pull_request_updated`` when ``updated_at`` moved forward but the head sha
  did not change. New comments, edits to the PR body, label changes, etc.
- ``commit_pushed`` when the head sha changed. This is the event review
  agents actually care about.

A handler callable receives each event. Handler errors are logged and
swallowed so one bad pull request does not stop the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from cli.logging import get_logger
from github_.models import PullRequestSummary
from github_.repository import RepositoryHandle
from github_.state import InMemoryStateStore, PollState, SeenPullRequest, StateStore

_log = get_logger(__name__)

EventKind = Literal["pull_request_opened", "pull_request_updated", "commit_pushed"]


@dataclass(frozen=True)
class PollEvent:
    """Something the polling service noticed."""

    kind: EventKind
    pull_request: PullRequestSummary

    @property
    def number(self) -> int:
        return self.pull_request.number


Handler = Callable[[PollEvent, RepositoryHandle], Awaitable[None]]


async def _noop_handler(event: PollEvent, handle: RepositoryHandle) -> None:
    _ = event, handle


class PollingService:
    """Polls a single repository for new and updated pull requests."""

    def __init__(
        self,
        handle: RepositoryHandle,
        *,
        interval_seconds: float,
        store: StateStore | None = None,
        handler: Handler | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")
        self._handle = handle
        self._interval = interval_seconds
        self._store: StateStore = store if store is not None else InMemoryStateStore()
        self._handler: Handler = handler if handler is not None else _noop_handler

    async def run_once(self) -> list[PollEvent]:
        """Run a single poll cycle and return the events that fired this round."""
        previous = self._store.load()
        prs = await self._handle.list_pull_requests(state="open")
        events = _diff(previous, prs)
        next_state = _project(previous, prs)
        self._store.save(next_state)

        _log.info(
            "polling.round",
            repo=self._handle.full_name,
            seen_open_prs=len(prs),
            events=len(events),
        )

        for event in events:
            try:
                await self._handler(event, self._handle)
            except Exception as exc:
                _log.error(
                    "polling.handler_error",
                    repo=self._handle.full_name,
                    event_kind=event.kind,
                    pr=event.number,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        return events

    async def run_forever(self) -> None:
        """Run :meth:`run_once` on the configured interval until cancelled."""
        _log.info(
            "polling.start",
            repo=self._handle.full_name,
            interval_seconds=self._interval,
        )
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                _log.info("polling.cancelled", repo=self._handle.full_name)
                raise
            except Exception as exc:
                _log.error(
                    "polling.round_error",
                    repo=self._handle.full_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            await asyncio.sleep(self._interval)


def _diff(previous: PollState, current: list[PullRequestSummary]) -> list[PollEvent]:
    """Compute the events that should fire for the transition from ``previous`` to ``current``."""
    events: list[PollEvent] = []

    # First-ever poll: do not flood the handler with events for every existing
    # open PR. Just seed state silently.
    if not previous.pull_requests:
        return events

    for pr in current:
        seen = previous.pull_requests.get(pr.number)
        if seen is None:
            events.append(PollEvent(kind="pull_request_opened", pull_request=pr))
            continue
        if pr.head.sha != seen.head_sha:
            events.append(PollEvent(kind="commit_pushed", pull_request=pr))
            continue
        if _isoformat(pr.updated_at) != seen.updated_at:
            events.append(PollEvent(kind="pull_request_updated", pull_request=pr))

    return events


def _project(previous: PollState, current: list[PullRequestSummary]) -> PollState:
    """Build the next state from the current poll results.

    Closed pull requests drop out: we only list open ones, so any number absent
    from ``current`` no longer needs to be tracked.
    """
    _ = previous
    return PollState(
        pull_requests={
            pr.number: SeenPullRequest(
                number=pr.number,
                updated_at=_isoformat(pr.updated_at),
                head_sha=pr.head.sha,
            )
            for pr in current
        }
    )


def _isoformat(value: datetime) -> str:
    """Normalize datetimes to the same ISO-8601 form for stable equality checks."""
    return value.isoformat()
