"""Implementation of ``openrabbit review --pr N``.

Pulls a single PR through OP-8's parser and prints a short summary. No agents
yet, that is Phase 4. The intent is to give a maintainer a way to confirm
the GitHub integration is working end to end before any model is wired in.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TextIO

from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import GitHubClient, PullRequestParser, RepositoryHandle

_log = get_logger(__name__)


async def run_review(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Fetch and parse one pull request, returning a summary dict.

    The returned dict is printed by the CLI. Returning structured data here
    keeps the function trivially testable without scraping stdout.
    """
    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    try:
        handle = RepositoryHandle.from_full_name(target, client)
        payload = await PullRequestParser(handle).parse(number)
    finally:
        await client.aclose()

    hunk_total = sum(len(f.hunks) for f in payload.files)
    binary_count = sum(1 for f in payload.files if f.is_binary)

    return {
        "repo": handle.full_name,
        "number": payload.number,
        "title": payload.pull_request.title,
        "state": payload.pull_request.state,
        "head_sha": payload.head_sha[:12],
        "files_changed": len(payload.files),
        "binary_files": binary_count,
        "hunks": hunk_total,
        "commits": len(payload.commits),
    }


def render_summary(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_review`."""
    print(f"PR #{summary['number']} on {summary['repo']}", file=out)
    print(f"  Title:        {summary['title']}", file=out)
    print(f"  State:        {summary['state']}", file=out)
    print(f"  Head SHA:     {summary['head_sha']}", file=out)
    print(
        f"  Files:        {summary['files_changed']} ({summary['binary_files']} binary)", file=out
    )
    print(f"  Hunks:        {summary['hunks']}", file=out)
    print(f"  Commits:      {summary['commits']}", file=out)


def run_review_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(run_review(settings, number=number, repo=repo, env=env))


# Keep an unused-import suppression so Path is available for type hints in
# callers that import this module. It costs nothing and keeps the public
# surface stable if we add file-output support later.
_ = Path
