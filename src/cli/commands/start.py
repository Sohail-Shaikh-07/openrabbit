"""Implementation of ``openrabbit start``.

Wires the OP-3 settings loader, the OP-6 GitHub client, the OP-7 repository
handle, and the OP-9 polling service into one foreground daemon. The handler
defaults to logging each event. Phase 4 will swap it for the real agent
pipeline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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


async def run_start(
    settings: Settings,
    *,
    workspace: Path,
    repo: str | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run the polling service in the foreground until cancelled."""
    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    handle = RepositoryHandle.from_full_name(target, client)
    state_path = workspace / STATE_SUBDIR / STATE_FILENAME
    store = FileStateStore(state_path)

    service = PollingService(
        handle,
        interval_seconds=settings.polling.interval_seconds,
        store=store,
        handler=_log_handler,
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
) -> None:
    """Synchronous wrapper used by the Typer command."""
    try:
        asyncio.run(run_start(settings, workspace=workspace, repo=repo, env=env))
    except KeyboardInterrupt:
        _log.info("start.shutdown")
