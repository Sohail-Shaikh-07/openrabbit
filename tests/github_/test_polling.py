"""Tests for ``github_.polling``."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from github_ import (
    GitHubClient,
    InMemoryStateStore,
    PollEvent,
    PollingService,
    PollState,
    RepositoryHandle,
    SeenPullRequest,
)

_BASE = "https://api.github.com"


def _client() -> GitHubClient:
    return GitHubClient(token="t0k3n", max_retries=2)


def _pr_summary(
    number: int,
    *,
    updated_at: str,
    head_sha: str,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"PR {number}",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "feat", "sha": head_sha, "label": "alice:feat"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": updated_at,
        "labels": [],
    }


def _mock_list(prs: list[dict[str, Any]]) -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(return_value=httpx.Response(200, json=prs))


def _service(
    *,
    store: InMemoryStateStore | None = None,
    handler: Any = None,
    max_concurrent_handlers: int = 1,
) -> tuple[PollingService, GitHubClient]:
    client = _client()
    handle = RepositoryHandle(owner="o", repo="r", client=client)
    service = PollingService(
        handle,
        interval_seconds=60,
        max_concurrent_handlers=max_concurrent_handlers,
        store=store,
        handler=handler,
    )
    return service, client


def test_constructor_rejects_zero_or_negative_interval() -> None:
    client = _client()
    handle = RepositoryHandle(owner="o", repo="r", client=client)
    with pytest.raises(ValueError):
        PollingService(handle, interval_seconds=0)
    with pytest.raises(ValueError):
        PollingService(handle, interval_seconds=-1)


def test_constructor_rejects_zero_or_negative_concurrency() -> None:
    client = _client()
    handle = RepositoryHandle(owner="o", repo="r", client=client)
    with pytest.raises(ValueError):
        PollingService(handle, interval_seconds=60, max_concurrent_handlers=0)
    with pytest.raises(ValueError):
        PollingService(handle, interval_seconds=60, max_concurrent_handlers=-1)


@respx.mock
async def test_first_poll_seeds_state_without_firing_events() -> None:
    _mock_list([_pr_summary(1, updated_at="2026-01-01T00:00:00Z", head_sha="a" * 40)])
    store = InMemoryStateStore()
    service, client = _service(store=store)

    try:
        events = await service.run_once()
    finally:
        await client.aclose()

    assert events == []
    assert 1 in store.load().pull_requests
    assert store.load().pull_requests[1].head_sha == "a" * 40


@respx.mock
async def test_new_pr_fires_opened_event() -> None:
    _mock_list(
        [
            _pr_summary(1, updated_at="2026-01-01T00:00:00Z", head_sha="a" * 40),
            _pr_summary(2, updated_at="2026-01-02T00:00:00Z", head_sha="c" * 40),
        ]
    )
    store = InMemoryStateStore(
        PollState(
            pull_requests={
                1: SeenPullRequest(
                    number=1, updated_at="2026-01-01T00:00:00+00:00", head_sha="a" * 40
                )
            }
        )
    )
    service, client = _service(store=store)

    try:
        events = await service.run_once()
    finally:
        await client.aclose()

    assert [(e.kind, e.number) for e in events] == [("pull_request_opened", 2)]


@respx.mock
async def test_updated_at_change_without_new_sha_fires_updated() -> None:
    _mock_list([_pr_summary(1, updated_at="2026-01-05T00:00:00Z", head_sha="a" * 40)])
    store = InMemoryStateStore(
        PollState(
            pull_requests={
                1: SeenPullRequest(
                    number=1, updated_at="2026-01-01T00:00:00+00:00", head_sha="a" * 40
                )
            }
        )
    )
    service, client = _service(store=store)

    try:
        events = await service.run_once()
    finally:
        await client.aclose()

    assert [(e.kind, e.number) for e in events] == [("pull_request_updated", 1)]


@respx.mock
async def test_new_head_sha_fires_commit_pushed() -> None:
    _mock_list([_pr_summary(1, updated_at="2026-01-05T00:00:00Z", head_sha="z" * 40)])
    store = InMemoryStateStore(
        PollState(
            pull_requests={
                1: SeenPullRequest(
                    number=1, updated_at="2026-01-01T00:00:00+00:00", head_sha="a" * 40
                )
            }
        )
    )
    service, client = _service(store=store)

    try:
        events = await service.run_once()
    finally:
        await client.aclose()

    assert [(e.kind, e.number) for e in events] == [("commit_pushed", 1)]


@respx.mock
async def test_handler_invoked_for_each_event() -> None:
    _mock_list(
        [
            _pr_summary(1, updated_at="2026-01-05T00:00:00Z", head_sha="z" * 40),
            _pr_summary(2, updated_at="2026-01-05T00:00:00Z", head_sha="c" * 40),
        ]
    )
    store = InMemoryStateStore(
        PollState(
            pull_requests={
                1: SeenPullRequest(
                    number=1, updated_at="2026-01-01T00:00:00+00:00", head_sha="a" * 40
                )
            }
        )
    )
    seen: list[PollEvent] = []

    async def handler(event: PollEvent, handle: RepositoryHandle) -> None:
        _ = handle
        seen.append(event)

    service, client = _service(store=store, handler=handler)

    try:
        await service.run_once()
    finally:
        await client.aclose()

    assert [(e.kind, e.number) for e in seen] == [
        ("pull_request_opened", 2),
        ("commit_pushed", 1),
    ] or [(e.kind, e.number) for e in seen] == [
        ("commit_pushed", 1),
        ("pull_request_opened", 2),
    ]


@respx.mock
async def test_handler_exception_does_not_break_remaining_events() -> None:
    _mock_list(
        [
            _pr_summary(1, updated_at="2026-01-05T00:00:00Z", head_sha="z" * 40),
            _pr_summary(2, updated_at="2026-01-05T00:00:00Z", head_sha="c" * 40),
        ]
    )
    store = InMemoryStateStore(
        PollState(
            pull_requests={
                1: SeenPullRequest(
                    number=1, updated_at="2026-01-01T00:00:00+00:00", head_sha="a" * 40
                )
            }
        )
    )
    invocations: list[int] = []

    async def handler(event: PollEvent, handle: RepositoryHandle) -> None:
        _ = handle
        invocations.append(event.number)
        if event.number == 1:
            raise RuntimeError("handler boom")

    service, client = _service(store=store, handler=handler)

    try:
        events = await service.run_once()
    finally:
        await client.aclose()

    assert len(events) == 2
    # Both handlers were invoked even though one raised.
    assert sorted(invocations) == [1, 2]


@respx.mock
async def test_handler_concurrency_is_bounded() -> None:
    _mock_list(
        [
            _pr_summary(1, updated_at="2026-01-05T00:00:00Z", head_sha="z" * 40),
            _pr_summary(2, updated_at="2026-01-05T00:00:00Z", head_sha="c" * 40),
        ]
    )
    store = InMemoryStateStore(
        PollState(
            pull_requests={
                1: SeenPullRequest(
                    number=1, updated_at="2026-01-01T00:00:00+00:00", head_sha="a" * 40
                )
            }
        )
    )
    active = 0
    max_active = 0

    async def handler(event: PollEvent, handle: RepositoryHandle) -> None:
        nonlocal active, max_active
        _ = event, handle
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1

    service, client = _service(store=store, handler=handler, max_concurrent_handlers=2)

    try:
        await service.run_once()
    finally:
        await client.aclose()

    assert max_active == 2


@respx.mock
async def test_closed_pr_drops_out_of_state() -> None:
    _mock_list([_pr_summary(2, updated_at="2026-01-02T00:00:00Z", head_sha="c" * 40)])
    store = InMemoryStateStore(
        PollState(
            pull_requests={
                1: SeenPullRequest(
                    number=1, updated_at="2026-01-01T00:00:00+00:00", head_sha="a" * 40
                ),
                2: SeenPullRequest(
                    number=2, updated_at="2026-01-02T00:00:00+00:00", head_sha="c" * 40
                ),
            }
        )
    )
    service, client = _service(store=store)

    try:
        await service.run_once()
    finally:
        await client.aclose()

    next_state = store.load()
    assert 1 not in next_state.pull_requests
    assert 2 in next_state.pull_requests
