"""Implementation of ``openrabbit start``.

Wires settings, GitHub access, polling state, and the review pipeline into one
foreground daemon. The daemon reviews new pull requests and new head commits,
while same-SHA metadata updates are logged and skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from cli.logging import get_logger
from configs.settings import Settings
from github_ import (
    FileStateStore,
    GitHubClient,
    PollEvent,
    PollingService,
    RepositoryHandle,
)

_log = get_logger(__name__)

STATE_SUBDIR = ".openrabbit"
STATE_FILENAME = "state.json"
ReviewRunner = Callable[..., Awaitable[dict[str, object]]]


class StartError(RuntimeError):
    """Raised when the start command cannot be wired up from settings."""


def format_start_banner(repo: str, interval: int, ver: str) -> str:
    """Return a one-line startup banner string for ``openrabbit start``."""
    return f"OpenRabbit {ver} | watching {repo} | polling every {interval}s"


def resolve_target_repo(settings: Settings, flag: str | None) -> str:
    """Pick the target repo. CLI flag wins over the setting; raise if neither set."""
    if flag:
        return flag
    if settings.repository.target:
        return settings.repository.target
    raise StartError(
        "no repository to watch. Pass --repo OWNER/REPO or set repository.target "
        "in .openrabbit/config.yml."
    )


async def _log_handler(event: PollEvent, handle: RepositoryHandle) -> None:
    _log.info(
        "polling.event",
        repo=handle.full_name,
        kind=event.kind,
        pr=event.number,
        title=event.pull_request.title,
    )


async def _run_manual_review(*args: Any, **kwargs: Any) -> dict[str, object]:
    """Import lazily to avoid a cycle with ``cli.commands.review``."""
    from cli.commands.review import run_review

    return await run_review(*args, **kwargs)


def build_review_handler(
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
) -> Callable[[PollEvent, RepositoryHandle], Awaitable[None]]:
    """Build the polling handler that reviews PRs with changed head SHAs."""
    runner = review_runner or _run_manual_review

    async def _handler(event: PollEvent, handle: RepositoryHandle) -> None:
        await _log_handler(event, handle)
        if event.kind == "pull_request_updated":
            _log.info(
                "start.review_skipped",
                repo=handle.full_name,
                pr=event.number,
                reason="head_sha_unchanged",
            )
            return

        summary = await runner(
            settings,
            number=event.number,
            repo=handle.full_name,
            env=env,
            dry_run=False,
        )
        _log.info(
            "start.review_complete",
            repo=handle.full_name,
            pr=event.number,
            findings=summary.get("findings_count", 0),
            comments_posted=summary.get("comments_posted", False),
        )

    return _handler


async def run_start(
    settings: Settings,
    *,
    workspace: Path,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
) -> None:
    """Run the polling service in the foreground until cancelled."""
    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    handle = RepositoryHandle.from_full_name(target, client)
    state_path = workspace / STATE_SUBDIR / STATE_FILENAME
    store = FileStateStore(state_path)

    handler = build_review_handler(settings, env=env, review_runner=review_runner)

    service = PollingService(
        handle,
        interval_seconds=settings.polling.interval_seconds,
        store=store,
        handler=handler,
    )

    _log.info(
        "start.ready",
        repo=handle.full_name,
        interval_seconds=settings.polling.interval_seconds,
        state=str(state_path),
    )

    try:
        await service.run_forever()
    finally:
        await client.aclose()


def run_start_blocking(
    settings: Settings,
    *,
    workspace: Path,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
) -> None:
    """Synchronous wrapper used by the Typer command."""
    try:
        asyncio.run(
            run_start(
                settings, workspace=workspace, repo=repo, env=env, review_runner=review_runner
            )
        )
    except KeyboardInterrupt:
        _log.info("start.shutdown")
