"""Implementation of ``openrabbit review --pr N``.

Pulls a single PR through the parser, runs the configured local review agents,
ranks their findings, prints a dry-run friendly summary, and publishes review
comments when not running in dry-run mode.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO

from cli.commands.review_pipeline import ReviewPipelineResult, run_agent_review
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import GitHubAuthError, GitHubClient, PullRequestParser, RepositoryHandle
from github_.publisher import GitHubPublisher
from memory.backends import PullRequestMemoryBackend
from memory.fingerprints import fingerprint_finding
from memory.history import PullRequestHistory
from memory.models import FindingComparison, FindingStatus
from memory.store import SQLitePullRequestMemory
from ranking.ranker import RankedFinding

_log = get_logger(__name__)


AgentRunner = Callable[..., Awaitable[ReviewPipelineResult]]
ReviewPublisher = Callable[..., Awaitable[None]]
ContextLoader = Callable[[Any], Awaitable[Any]]


class ReviewMode(StrEnum):
    """Controls whether review publishing is full or incremental."""

    FULL = "full"
    INCREMENTAL = "incremental"


async def run_review(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
    mode: ReviewMode | str = ReviewMode.INCREMENTAL,
    run_agents: bool = True,
    agent_runner: AgentRunner | None = None,
    publisher: ReviewPublisher | None = None,
    context_loader: ContextLoader | None = None,
    memory_store: PullRequestMemoryBackend | None = None,
) -> dict[str, object]:
    """Fetch, review, optionally publish, and return a summary dict.

    The returned dict is printed by the CLI. Returning structured data here
    keeps the function trivially testable without scraping stdout.
    """
    target = resolve_target_repo(settings, repo)
    review_mode = _coerce_review_mode(mode)
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
    memory_enabled = settings.memory.enabled
    memory_comparison: FindingComparison | None = None
    memory_error: str | None = None
    memory_store_for_run: PullRequestMemoryBackend | None = None
    pr_history: PullRequestHistory | None = None

    if memory_enabled:
        try:
            memory_store_for_run = memory_store or SQLitePullRequestMemory(
                settings.resolved_memory_path()
            )
            local_history = memory_store_for_run.load_history(handle.full_name, payload.number)
            pr_history = PullRequestHistory.from_payload(
                repo=handle.full_name,
                payload=payload,
                local=local_history,
                learnings=_load_repo_learnings(settings, memory_store_for_run, handle.full_name),
            )
        except Exception as exc:
            memory_error = type(exc).__name__
            _log.warning(
                "review.memory_load_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

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
            payload,
            settings=settings,
            retrieval_result=retrieval_result,
            pr_history=pr_history,
            env=env,
        )
        ranked = pipeline_result.ranked_findings
        dropped_findings_count = pipeline_result.dropped_findings_count
        skipped_paths = pipeline_result.skipped_paths or []
    else:
        skipped_paths = []

    context_loaded = _has_retrieval_context(retrieval_result)
    if memory_enabled and memory_store_for_run is not None:
        try:
            memory_comparison = memory_store_for_run.compare_with_history(
                repo=handle.full_name,
                pr_number=payload.number,
                head_sha=payload.head_sha,
                current_findings=[rf.finding for rf in ranked],
            )
        except Exception as exc:
            memory_error = type(exc).__name__
            _log.warning(
                "review.memory_compare_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    publish_ranked = _publishable_ranked(ranked, mode=review_mode, comparison=memory_comparison)
    comments_posted = False
    publish_status = "dry_run" if dry_run else "no_findings"
    if not dry_run and publish_ranked:
        await _publish_review(
            settings,
            env=env,
            handle=handle,
            pr_number=payload.number,
            ranked=publish_ranked,
            head_sha=payload.head_sha,
            publisher=publisher,
        )
        comments_posted = True
        publish_status = "posted"
    elif not dry_run and ranked and review_mode is ReviewMode.INCREMENTAL:
        publish_status = "no_new_findings"

    if memory_enabled:
        try:
            store = (
                memory_store_for_run
                or memory_store
                or SQLitePullRequestMemory(settings.resolved_memory_path())
            )
            write = store.record_review(
                repo=handle.full_name,
                pr_number=payload.number,
                head_sha=payload.head_sha,
                findings=[rf.finding for rf in ranked],
                context_loaded=context_loaded,
                comments_posted=comments_posted,
            )
            memory_comparison = write.comparison
        except Exception as exc:
            memory_error = type(exc).__name__
            _log.warning(
                "review.memory_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

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
        "mode": review_mode.value,
        "findings_count": len(ranked),
        "published_findings_count": 0 if dry_run else len(publish_ranked),
        "dropped_findings_count": dropped_findings_count,
        "skipped_paths_count": len(skipped_paths),
        "skipped_paths": skipped_paths,
        "context_loaded": context_loaded,
        "context_provenance": _context_provenance(retrieval_result),
        "findings": _serialize_ranked_findings(ranked, memory_comparison),
        "comments_posted": comments_posted,
        "publish_status": publish_status,
        "memory_enabled": memory_enabled,
        "memory_status_counts": _memory_status_counts(memory_comparison),
        "memory_error": memory_error,
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
    mode = summary.get("mode")
    if isinstance(mode, str):
        print(f"  Mode:         {mode}", file=out)
    raw_findings = summary.get("findings")
    findings = raw_findings if isinstance(raw_findings, list) else []
    print(f"  Findings:     {len(findings)}", file=out)
    dropped = summary.get("dropped_findings_count")
    if isinstance(dropped, int) and dropped > 0:
        print(f"  Dropped:      {dropped} ungrounded", file=out)
    skipped_count = summary.get("skipped_paths_count")
    raw_skipped = summary.get("skipped_paths")
    skipped_paths = raw_skipped if isinstance(raw_skipped, list) else []
    if isinstance(skipped_count, int) and skipped_count > 0:
        noun = "path" if skipped_count == 1 else "paths"
        print(f"  Skipped:     {skipped_count} {noun}", file=out)
        for item in skipped_paths[:5]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            reason = str(item.get("reason", "")).strip()
            print(f"    - {path} ({reason})", file=out)
    context_loaded = summary.get("context_loaded")
    if isinstance(context_loaded, bool):
        print(f"  Context:      {'loaded' if context_loaded else 'diff only'}", file=out)
    raw_provenance = summary.get("context_provenance")
    provenance = raw_provenance if isinstance(raw_provenance, list) else []
    if provenance:
        print("  Context sources:", file=out)
        for item in provenance[:5]:
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension", "")).strip()
            source_path = str(item.get("source_path", "")).strip()
            name = str(item.get("name", "")).strip()
            score = item.get("score")
            score_text = f", score={score:.2f}" if isinstance(score, int | float) else ""
            label = f"{dimension} {source_path}".strip()
            detail = f" ({name}{score_text})" if name or score_text else ""
            print(f"    - {label}{detail}", file=out)
    publish_status = summary.get("publish_status")
    if publish_status == "posted":
        print("  Published:    yes", file=out)
    elif publish_status == "no_findings":
        print("  Published:    no findings to post", file=out)
    elif publish_status == "no_new_findings":
        print("  Published:    no new findings to post", file=out)
    elif publish_status == "dry_run":
        print("  Published:    no (dry run)", file=out)
    memory_enabled = summary.get("memory_enabled")
    if isinstance(memory_enabled, bool):
        print(f"  Memory:       {'enabled' if memory_enabled else 'disabled'}", file=out)
    memory_error = summary.get("memory_error")
    if isinstance(memory_error, str) and memory_error:
        print(f"  Memory error: {memory_error}", file=out)
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
    mode: ReviewMode | str = ReviewMode.INCREMENTAL,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(
        run_review(settings, number=number, repo=repo, env=env, dry_run=dry_run, mode=mode)
    )


def _coerce_review_mode(mode: ReviewMode | str) -> ReviewMode:
    if isinstance(mode, ReviewMode):
        return mode
    try:
        return ReviewMode(str(mode).strip().lower())
    except ValueError as exc:
        raise ValueError("mode must be 'full' or 'incremental'") from exc


def _serialize_ranked_finding(
    ranked: RankedFinding,
    *,
    memory_status: FindingStatus | None = None,
) -> dict[str, object]:
    finding = ranked.finding.as_dict()
    finding["score"] = ranked.score
    if memory_status is not None:
        finding["memory_status"] = memory_status.value
    return finding


def _serialize_ranked_findings(
    ranked: list[RankedFinding],
    memory_comparison: FindingComparison | None,
) -> list[dict[str, object]]:
    statuses: dict[str, FindingStatus] = {}
    if memory_comparison is not None:
        statuses = {record.fingerprint: record.status for record in memory_comparison.current}
    return [
        _serialize_ranked_finding(
            rf,
            memory_status=statuses.get(fingerprint_finding(rf.finding)),
        )
        for rf in ranked
    ]


def _context_provenance(retrieval_result: Any | None) -> list[dict[str, object]]:
    if retrieval_result is None:
        return []
    provenance = getattr(retrieval_result, "provenance", None)
    if not callable(provenance):
        return []
    try:
        rows = provenance()
    except Exception as exc:
        _log.warning(
            "review.context_provenance_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _memory_status_counts(memory_comparison: FindingComparison | None) -> dict[str, int]:
    if memory_comparison is None:
        return {}
    counts: dict[str, int] = {}
    for record in memory_comparison.current:
        counts[record.status.value] = counts.get(record.status.value, 0) + 1
    if memory_comparison.resolved:
        counts[FindingStatus.POSSIBLY_FIXED.value] = len(memory_comparison.resolved)
    return counts


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


def _publishable_ranked(
    ranked: list[RankedFinding],
    *,
    mode: ReviewMode,
    comparison: FindingComparison | None,
) -> list[RankedFinding]:
    if mode is ReviewMode.FULL or comparison is None:
        return list(ranked)

    publishable_fingerprints = {
        record.fingerprint for record in comparison.current if record.status is FindingStatus.NEW
    }
    return [rf for rf in ranked if fingerprint_finding(rf.finding) in publishable_fingerprints]


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
