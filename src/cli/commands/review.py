"""Implementation of ``openrabbit review --pr N``.

Pulls a single PR through the parser, runs the configured local review agents,
ranks their findings, and prints a dry-run friendly summary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TextIO

from cli.commands.review_pipeline import ReviewPipelineResult, run_agent_review
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import GitHubClient, PullRequestParser, RepositoryHandle
from ranking.ranker import RankedFinding

_log = get_logger(__name__)


AgentRunner = Callable[..., Awaitable[ReviewPipelineResult]]


async def run_review(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
    run_agents: bool = True,
    agent_runner: AgentRunner | None = None,
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
    ranked: list[RankedFinding] = []
    dropped_findings_count = 0

    if run_agents:
        runner = agent_runner or run_agent_review
        pipeline_result = await runner(payload, settings=settings)
        ranked = pipeline_result.ranked_findings
        dropped_findings_count = pipeline_result.dropped_findings_count

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
        "dry_run": dry_run,
        "findings_count": len(ranked),
        "dropped_findings_count": dropped_findings_count,
        "findings": [_serialize_ranked_finding(rf) for rf in ranked],
        "comments_posted": False,
    }


def render_summary(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_review`."""
    if summary.get("dry_run"):
        print("[DRY RUN] No comments will be posted.", file=out)
    print(f"PR #{summary['number']} on {summary['repo']}", file=out)
    print(f"  Title:        {summary['title']}", file=out)
    print(f"  State:        {summary['state']}", file=out)
    print(f"  Head SHA:     {summary['head_sha']}", file=out)
    print(
        f"  Files:        {summary['files_changed']} ({summary['binary_files']} binary)", file=out
    )
    print(f"  Hunks:        {summary['hunks']}", file=out)
    print(f"  Commits:      {summary['commits']}", file=out)
    raw_findings = summary.get("findings")
    findings = raw_findings if isinstance(raw_findings, list) else []
    print(f"  Findings:     {len(findings)}", file=out)
    dropped = summary.get("dropped_findings_count")
    if isinstance(dropped, int) and dropped > 0:
        print(f"  Dropped:      {dropped} ungrounded", file=out)
    if findings:
        print("", file=out)
        print("Model findings:", file=out)
    for item in findings:
        location = f"{item['file']}:{item['line']}" if item.get("line") else str(item["file"])
        print(f"  - [{str(item['severity']).upper()}] {item['title']} ({location})", file=out)
        print(f"    {item['reason']}", file=out)
        print(f"    Suggestion: {item['suggestion']}", file=out)


def run_review_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(run_review(settings, number=number, repo=repo, env=env, dry_run=dry_run))


def _serialize_ranked_finding(ranked: RankedFinding) -> dict[str, object]:
    finding = ranked.finding.as_dict()
    finding["score"] = ranked.score
    return finding


# Keep an unused-import suppression so Path is available for type hints in
# callers that import this module. It costs nothing and keeps the public
# surface stable if we add file-output support later.
_ = Path
