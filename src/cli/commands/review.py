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

from cli.commands.history import load_pr_history
from cli.commands.review_context import (
    filter_model_review_context,
    repository_path_key,
    skipped_path_keys,
)
from cli.commands.review_pipeline import ReviewPipelineResult, run_agent_review
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.schema import QualitySettings
from configs.settings import Settings
from github_ import GitHubAuthError, GitHubClient, PullRequestParser, RepositoryHandle
from github_.publisher import GitHubPublisher
from knowledge.context import load_connector_context
from knowledge.diagnostics import build_context_precision_diagnostics
from memory.backends import (
    PullRequestMemoryBackend,
    compare_with_history_compat,
    paths_match_any,
    record_review_compat,
)
from memory.fingerprints import fingerprint_finding
from memory.history import PullRequestHistory
from memory.models import FindingComparison, FindingStatus
from memory.store import SQLitePullRequestMemory
from quality.models import ToolRunResult
from quality.runner import LocalQualityRunner
from rag.retriever import RetrievalResult
from rag.scanner import FileKind, RepositoryScanner
from ranking.ranker import RankedFinding
from review_controls import prepare_review_controls

_log = get_logger(__name__)
_MAX_DIRECT_GUIDELINES = 8
_MAX_DIRECT_GUIDELINE_CHARS = 4000


AgentRunner = Callable[..., Awaitable[ReviewPipelineResult]]
ReviewPublisher = Callable[..., Awaitable[None]]
ContextLoader = Callable[[Any], Awaitable[Any]]
QualityGateRunner = Callable[[Path, QualitySettings], Awaitable[list[ToolRunResult]]]


class ReviewMode(StrEnum):
    """Controls whether review publishing is full or incremental."""

    FULL = "full"
    INCREMENTAL = "incremental"


async def run_local_quality_gates(
    workspace: Path,
    settings: QualitySettings,
) -> list[ToolRunResult]:
    """Run bounded quality tools without blocking the review event loop."""
    runner = LocalQualityRunner(settings)
    return await asyncio.to_thread(runner.run, workspace)


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
    quality_gate_runner: QualityGateRunner | None = None,
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
        original_payload = payload
        controls_result = await prepare_review_controls(
            payload,
            settings.review,
            source_loader=handle.get_file_text,
        )
        payload = controls_result.filtered_payload
        pr_history_result = await load_pr_history(
            settings,
            handle=handle,
            payload=payload,
            memory_store=memory_store,
        )
    finally:
        await client.aclose()

    hunk_total = sum(len(f.hunks) for f in original_payload.files)
    binary_count = sum(1 for f in original_payload.files if f.is_binary)
    ranked: list[RankedFinding] = []
    dropped_findings_count = 0
    retrieval_result: Any | None = None
    memory_enabled = settings.memory.enabled
    memory_comparison: FindingComparison | None = None
    memory_error: str | None = None
    memory_store_for_run = pr_history_result.store
    pr_history = pr_history_result.history
    memory_error = pr_history_result.error
    quality_results: list[ToolRunResult] = []
    quality_error: str | None = None
    skipped_paths = [item.as_dict() for item in controls_result.skipped_paths]
    connector_context_summary: dict[str, object] = {}
    model_pr_history = pr_history
    model_quality_results = quality_results

    if run_agents and settings.quality.enabled:
        quality_runner = quality_gate_runner or run_local_quality_gates
        try:
            quality_results = await quality_runner(
                settings.resolved_workspace_root(),
                settings.quality,
            )
        except Exception as exc:
            quality_error = type(exc).__name__
            _log.warning(
                "review.quality_gates_failed",
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
        retrieval_result = _merge_direct_repository_guidelines(
            retrieval_result,
            workspace=settings.resolved_workspace_root(),
            pr_payload=payload,
        )
        connector_context = load_connector_context(
            settings,
            payload,
            repo=handle.full_name,
            env=env,
            retrieval_result=retrieval_result,
        )
        retrieval_result = connector_context.retrieval_result
        connector_context_summary = connector_context.summary
        model_context = filter_model_review_context(
            controls_result,
            retrieval_result=retrieval_result,
            pr_history=pr_history,
            quality_results=quality_results,
        )
        retrieval_result = model_context.retrieval_result
        model_pr_history = model_context.pr_history
        model_quality_results = model_context.quality_results
        if agent_runner is None:
            pipeline_result = await run_agent_review(
                payload,
                settings=settings,
                retrieval_result=retrieval_result,
                pr_history=model_pr_history,
                quality_results=model_quality_results,
                controls_result=controls_result,
                env=env,
            )
        else:
            pipeline_result = await agent_runner(
                payload,
                settings=settings,
                retrieval_result=retrieval_result,
                pr_history=model_pr_history,
                quality_results=model_quality_results,
                env=env,
            )
        ranked = pipeline_result.ranked_findings
        dropped_findings_count = pipeline_result.dropped_findings_count
        if agent_runner is not None:
            ranked, skipped_findings = _exclude_skipped_ranked(
                ranked,
                skipped_path_keys(controls_result),
                repository_paths={
                    repository_path_key(file_.path)
                    for file_ in controls_result.filtered_payload.files
                }
                | {item.path for item in controls_result.skipped_paths},
            )
            dropped_findings_count += skipped_findings
        if pipeline_result.skipped_paths is not None:
            skipped_paths = pipeline_result.skipped_paths
    context_loaded = _has_retrieval_context(retrieval_result)
    if memory_enabled and memory_store_for_run is not None:
        try:
            memory_comparison = compare_with_history_compat(
                memory_store_for_run,
                repo=handle.full_name,
                pr_number=payload.number,
                head_sha=payload.head_sha,
                current_findings=[rf.finding for rf in ranked],
                preserve_paths=tuple(item.path for item in controls_result.skipped_paths),
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
            write = record_review_compat(
                store,
                repo=handle.full_name,
                pr_number=payload.number,
                head_sha=payload.head_sha,
                findings=[rf.finding for rf in ranked],
                context_loaded=context_loaded,
                comments_posted=comments_posted,
                preserve_paths=tuple(item.path for item in controls_result.skipped_paths),
            )
            memory_comparison = write.comparison
        except Exception as exc:
            memory_error = type(exc).__name__
            _log.warning(
                "review.memory_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    context_provenance = _context_provenance(retrieval_result)
    context_diagnostics = build_context_precision_diagnostics(
        retrieval_result,
        connector_context=connector_context_summary,
        pr_payload=payload,
        pr_history=model_pr_history,
        quality_results=model_quality_results,
        command="review",
    )
    return {
        "repo": handle.full_name,
        "number": payload.number,
        "title": payload.pull_request.title,
        "state": payload.pull_request.state,
        "head_sha": payload.head_sha[:12],
        "files_changed": len(original_payload.files),
        "binary_files": binary_count,
        "hunks": hunk_total,
        "commits": len(original_payload.commits),
        "dry_run": dry_run,
        "mode": review_mode.value,
        "findings_count": len(ranked),
        "published_findings_count": 0 if dry_run else len(publish_ranked),
        "dropped_findings_count": dropped_findings_count,
        "skipped_paths_count": len(skipped_paths),
        "skipped_paths": skipped_paths,
        "ast_instruction_count": len(controls_result.ast_matches),
        "review_control_warning_count": len(controls_result.warnings),
        "review_control_warnings": [item.as_dict() for item in controls_result.warnings],
        "ast_unsupported_path_count": len(controls_result.unsupported_paths),
        "context_loaded": context_loaded,
        "context_provenance": context_provenance,
        "context_diagnostics": context_diagnostics,
        "connector_context": connector_context_summary,
        "findings": _serialize_ranked_findings(ranked, memory_comparison),
        "comments_posted": comments_posted,
        "publish_status": publish_status,
        "memory_enabled": memory_enabled,
        "memory_context": _memory_context_label(memory_enabled, pr_history, memory_error),
        "last_reviewed_sha": _last_reviewed_sha(pr_history),
        "learning_count": pr_history_result.learning_count,
        "conversation_count": pr_history_result.conversation_count,
        "guideline_sources": _guideline_sources(context_provenance),
        "linked_issue_count": len(original_payload.linked_issues),
        "memory_status_counts": _memory_status_counts(memory_comparison),
        "memory_error": memory_error,
        "quality_gates": [result.as_dict() for result in quality_results],
        "quality_status_counts": _quality_status_counts(quality_results),
        "quality_diagnostics_count": sum(len(result.diagnostics) for result in quality_results),
        "quality_error": quality_error,
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
    ast_instruction_count = summary.get("ast_instruction_count")
    if isinstance(ast_instruction_count, int) and ast_instruction_count > 0:
        print(f"  AST rules: {ast_instruction_count} matched", file=out)
    warning_count = summary.get("review_control_warning_count")
    if isinstance(warning_count, int) and warning_count > 0:
        print(f"  Control warnings: {warning_count}", file=out)
    unsupported_count = summary.get("ast_unsupported_path_count")
    if isinstance(unsupported_count, int) and unsupported_count > 0:
        print(f"  Unsupported AST files: {unsupported_count}", file=out)
    context_loaded = summary.get("context_loaded")
    if isinstance(context_loaded, bool):
        print(f"  Context:      {'loaded' if context_loaded else 'diff only'}", file=out)
    quality_statuses = summary.get("quality_status_counts")
    if isinstance(quality_statuses, dict) and quality_statuses:
        status_text = ", ".join(
            f"{status}={count}"
            for status, count in sorted(quality_statuses.items())
            if isinstance(count, int) and count > 0
        )
        diagnostics = summary.get("quality_diagnostics_count", 0)
        if status_text:
            print(f"  Quality:      {status_text}; diagnostics={diagnostics}", file=out)
    quality_error = summary.get("quality_error")
    if isinstance(quality_error, str) and quality_error:
        print(f"  Quality error:{quality_error}", file=out)
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
            reason = str(item.get("retrieval_reason", "")).strip()
            score = item.get("score")
            score_text = f", score={score:.2f}" if isinstance(score, int | float) else ""
            reason_text = f", reason={reason}" if reason else ""
            label = f"{dimension} {source_path}".strip()
            detail_text = f"{name}{score_text}{reason_text}"
            detail = f" ({detail_text})" if detail_text else ""
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
    last_reviewed_sha = summary.get("last_reviewed_sha")
    if isinstance(last_reviewed_sha, str) and last_reviewed_sha:
        print(f"  Last review:  {last_reviewed_sha[:12]}", file=out)
    raw_status_counts = summary.get("memory_status_counts")
    status_counts = raw_status_counts if isinstance(raw_status_counts, dict) else {}
    if status_counts:
        status_text = ", ".join(
            f"{status}={count}"
            for status, count in sorted(status_counts.items())
            if isinstance(count, int) and count > 0
        )
        if status_text:
            print(f"  Statuses:     {status_text}", file=out)
    conversation_count = summary.get("conversation_count")
    if isinstance(conversation_count, int) and conversation_count > 0:
        noun = "event" if conversation_count == 1 else "events"
        print(f"  Conversation: {conversation_count} {noun}", file=out)
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


def _quality_status_counts(results: list[ToolRunResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = result.status.value
        counts[status] = counts.get(status, 0) + 1
    return counts


def _exclude_skipped_ranked(
    ranked: list[RankedFinding],
    skipped: set[str],
    *,
    repository_paths: set[str] | None = None,
) -> tuple[list[RankedFinding], int]:
    known_paths = repository_paths or set()
    kept = [
        item
        for item in ranked
        if not paths_match_any(item.finding.file, skipped, repository_paths=known_paths)
    ]
    return kept, len(ranked) - len(kept)


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


def _memory_context_label(
    enabled: bool,
    history: PullRequestHistory | None,
    error: str | None,
) -> str:
    if not enabled:
        return "disabled"
    if error:
        return "error"
    if history is not None:
        return "loaded"
    return "unavailable"


def _last_reviewed_sha(history: PullRequestHistory | None) -> str:
    if history is None or history.local is None or history.local.last_reviewed_sha is None:
        return ""
    return history.local.last_reviewed_sha


def _guideline_sources(provenance: list[dict[str, object]]) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for row in provenance:
        if row.get("rule_source") != "repository_guideline":
            continue
        source = str(row.get("guideline_path") or row.get("source_path") or "").strip()
        if not source or source in seen:
            continue
        seen.add(source)
        sources.append(source)
    return sources


def _merge_direct_repository_guidelines(
    retrieval_result: Any | None,
    *,
    workspace: Path,
    pr_payload: Any,
) -> Any | None:
    """Add local repository guideline files even when vector context is unavailable."""
    guidelines = _direct_repository_guideline_hits(workspace, pr_payload)
    if not guidelines:
        return retrieval_result
    if retrieval_result is None:
        return RetrievalResult(security=guidelines)
    security = getattr(retrieval_result, "security", None)
    if not isinstance(security, list):
        return retrieval_result
    seen: set[str] = set()
    for hit in security:
        if not isinstance(hit, dict):
            continue
        existing_payload = hit.get("payload")
        if not isinstance(existing_payload, dict):
            continue
        source = str(
            existing_payload.get("guideline_path") or existing_payload.get("source_path") or ""
        )
        if source:
            seen.add(source)
    for hit in guidelines:
        payload = hit.get("payload")
        if not isinstance(payload, dict):
            continue
        source = str(payload.get("guideline_path") or payload.get("source_path"))
        if source and source not in seen:
            security.append(hit)
            seen.add(source)
    return retrieval_result


def _direct_repository_guideline_hits(workspace: Path, pr_payload: Any) -> list[dict[str, object]]:
    changed_paths = _changed_paths(pr_payload)
    hits: list[dict[str, object]] = []
    try:
        records = RepositoryScanner().scan(workspace)
    except Exception as exc:
        _log.warning(
            "review.guideline_scan_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []
    for record in records:
        if record.kind is not FileKind.rules:
            continue
        if record.metadata.get("rule_source") != "repository_guideline":
            continue
        scope = record.metadata.get("scope_path", ".")
        if not _guideline_applies(scope, changed_paths):
            continue
        try:
            text = record.absolute_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = text.strip()
        if not text:
            continue
        source_path = record.path.as_posix()
        hits.append(
            {
                "id": f"direct-guideline:{source_path}",
                "score": 1.0,
                "payload": {
                    "name": source_path,
                    "source_path": source_path,
                    "kind": "repository_guideline",
                    "text": _bounded_guideline_text(text),
                    "rule_source": "repository_guideline",
                    "scope_path": scope or ".",
                    "guideline_path": record.metadata.get("guideline_path", source_path),
                    "retrieval_reason": (
                        "scoped_guideline" if scope and scope != "." else "repository_guideline"
                    ),
                },
            }
        )
        if len(hits) >= _MAX_DIRECT_GUIDELINES:
            break
    return hits


def _changed_paths(pr_payload: Any) -> tuple[str, ...]:
    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list):
        return ()
    paths: list[str] = []
    for file_ in files:
        path = str(getattr(file_, "path", "") or "").strip().replace("\\", "/")
        if path:
            paths.append(path)
    return tuple(paths)


def _guideline_applies(scope: str | None, changed_paths: tuple[str, ...]) -> bool:
    clean_scope = (scope or ".").strip().replace("\\", "/").strip("/")
    if clean_scope in {"", "."}:
        return True
    prefix = f"{clean_scope}/"
    return any(path == clean_scope or path.startswith(prefix) for path in changed_paths)


def _bounded_guideline_text(text: str) -> str:
    if len(text) <= _MAX_DIRECT_GUIDELINE_CHARS:
        return text
    return text[:_MAX_DIRECT_GUIDELINE_CHARS].rsplit("\n", 1)[0].strip()


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
