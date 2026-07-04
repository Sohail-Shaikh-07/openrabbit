"""Implementation of ``openrabbit review --pr N``.

Pulls a single PR through the parser, runs the configured local review agents,
ranks their findings, prints a dry-run friendly summary, and publishes review
comments when not running in dry-run mode.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TextIO

from cli.commands.review_pipeline import ReviewPipelineResult, run_agent_review
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import GitHubAuthError, GitHubClient, PullRequestParser, RepositoryHandle
from github_.publisher import GitHubPublisher
from ranking.ranker import RankedFinding

_log = get_logger(__name__)


AgentRunner = Callable[..., Awaitable[ReviewPipelineResult]]
ReviewPublisher = Callable[..., Awaitable[None]]
ContextLoader = Callable[[Any], Awaitable[Any]]


async def run_review(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
    run_agents: bool = True,
    agent_runner: AgentRunner | None = None,
    publisher: ReviewPublisher | None = None,
    context_loader: ContextLoader | None = None,
) -> dict[str, object]:
    """Fetch, review, optionally publish, and return a summary dict.

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
    retrieval_result: Any | None = None

    if run_agents:
        loader = context_loader or _load_review_context
        try:
            retrieval_result = await loader(payload)
        except Exception as exc:
            _log.warning(
                "review.context_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            retrieval_result = None
        runner = agent_runner or run_agent_review
        pipeline_result = await runner(
            payload, settings=settings, retrieval_result=retrieval_result, env=env
        )
        ranked = pipeline_result.ranked_findings
        dropped_findings_count = pipeline_result.dropped_findings_count

    context_loaded = _has_retrieval_context(retrieval_result)
    comments_posted = False
    publish_status = "dry_run" if dry_run else "no_findings"
    if not dry_run and ranked:
        await _publish_review(
            settings,
            env=env,
            handle=handle,
            pr_number=payload.number,
            ranked=ranked,
            head_sha=payload.head_sha,
            publisher=publisher,
        )
        comments_posted = True
        publish_status = "posted"

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
        "context_loaded": context_loaded,
        "findings": [_serialize_ranked_finding(rf) for rf in ranked],
        "comments_posted": comments_posted,
        "publish_status": publish_status,
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
    context_loaded = summary.get("context_loaded")
    if isinstance(context_loaded, bool):
        print(f"  Context:      {'loaded' if context_loaded else 'diff only'}", file=out)
    publish_status = summary.get("publish_status")
    if publish_status == "posted":
        print("  Published:    yes", file=out)
    elif publish_status == "no_findings":
        print("  Published:    no findings to post", file=out)
    elif publish_status == "dry_run":
        print("  Published:    no (dry run)", file=out)
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


async def _load_review_context(pr_payload: Any) -> Any:
    """Load repository-aware RAG context for *pr_payload*.

    The underlying retriever catches Qdrant/index/embedding errors and returns
    an empty result, so review execution can continue with the diff alone.
    """
    store: Any | None = None
    try:
        from rag.embeddings import EmbeddingEngine
        from rag.retriever import ContextRetriever
        from rag.vector_store import VectorStore

        store = VectorStore()
        retriever = ContextRetriever(engine=EmbeddingEngine(), store=store)
        return await retriever.retrieve(pr_payload)
    except Exception as exc:
        _log.warning(
            "review.context_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None
    finally:
        if store is not None:
            try:
                await store.close()
            except Exception as exc:
                _log.warning(
                    "review.context_close_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )


def _has_retrieval_context(retrieval_result: Any | None) -> bool:
    if retrieval_result is None:
        return False
    for dimension in ("security", "architecture", "performance", "tests"):
        value = getattr(retrieval_result, dimension, None)
        if isinstance(value, list) and value:
            return True
    return False


async def _publish_review(
    settings: Settings,
    *,
    env: dict[str, str] | None,
    handle: RepositoryHandle,
    pr_number: int,
    ranked: list[RankedFinding],
    head_sha: str,
    publisher: ReviewPublisher | None,
) -> None:
    """Post *ranked* findings to GitHub using the configured repository."""
    if publisher is not None:
        await publisher(pr_number=pr_number, ranked=ranked, head_sha=head_sha)
        return

    token = settings.resolved_github_token(env=env)
    if token is None:
        # This should not happen after GitHubClient.from_settings succeeded, but
        # keep the failure explicit if a future caller changes the fetch path.
        raise GitHubAuthError("cannot publish review without a resolved GitHub token")

    await GitHubPublisher(token=token, owner=handle.owner, repo=handle.repo).publish(
        pr_number=pr_number,
        ranked=ranked,
        head_sha=head_sha,
    )


# Keep an unused-import suppression so Path is available for type hints in
# callers that import this module. It costs nothing and keeps the public
# surface stable if we add file-output support later.
_ = Path
