"""Path-scoped filtering for model-facing pull request context."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from memory.backends import paths_match_any
from memory.backends import repository_path_key as memory_repository_path_key
from memory.history import PullRequestHistory
from quality.models import ToolRunResult
from rag.retriever import RetrievalResult
from review_controls import ReviewControlResult


@dataclass(frozen=True)
class ModelReviewContext:
    """Auxiliary context safe to pass to a model for one prepared review."""

    retrieval_result: Any | None
    pr_history: PullRequestHistory | None
    quality_results: list[ToolRunResult]


def filter_model_review_context(
    controls_result: ReviewControlResult,
    *,
    retrieval_result: Any | None,
    pr_history: PullRequestHistory | None,
    quality_results: list[ToolRunResult] | None = None,
) -> ModelReviewContext:
    """Remove auxiliary items tied to skipped changed paths only."""
    skipped = skipped_path_keys(controls_result)
    repository_paths = _control_repository_paths(controls_result)
    quality = quality_results or []
    if not skipped:
        return ModelReviewContext(retrieval_result, pr_history, quality)
    return ModelReviewContext(
        retrieval_result=_filter_retrieval(retrieval_result, skipped, repository_paths),
        pr_history=_filter_history(pr_history, skipped, repository_paths),
        quality_results=_filter_quality(quality, skipped, repository_paths),
    )


def skipped_path_keys(controls_result: ReviewControlResult) -> set[str]:
    """Return normalized repository paths skipped by prepared controls."""
    return {
        key for item in controls_result.skipped_paths if (key := repository_path_key(item.path))
    }


def repository_path_key(value: object) -> str:
    """Normalize a repository-relative path for exact comparisons."""
    return memory_repository_path_key(value)


def _control_repository_paths(controls_result: ReviewControlResult) -> set[str]:
    files = getattr(controls_result.filtered_payload, "files", [])
    paths = [getattr(file_, "path", "") for file_ in files]
    paths.extend(item.path for item in controls_result.skipped_paths)
    return {key for path in paths if (key := memory_repository_path_key(path))}


def _filter_retrieval(
    value: Any | None,
    skipped: set[str],
    repository_paths: set[str],
) -> Any | None:
    if not isinstance(value, RetrievalResult):
        return value
    dimensions = {
        name: [
            hit
            for hit in hits
            if not paths_match_any(_retrieval_path(hit), skipped, repository_paths=repository_paths)
        ]
        for name, hits in value.as_dict().items()
    }
    if all(dimensions[str(name)] == hits for name, hits in value.as_dict().items()):
        return value
    return RetrievalResult(
        security=dimensions["security"],
        architecture=dimensions["architecture"],
        performance=dimensions["performance"],
        tests=dimensions["tests"],
    )


def _retrieval_path(hit: object) -> str:
    if not isinstance(hit, dict):
        return ""
    payload = hit.get("payload")
    if not isinstance(payload, dict):
        return ""
    return repository_path_key(payload.get("source_path"))


def _filter_history(
    history: PullRequestHistory | None,
    skipped: set[str],
    repository_paths: set[str],
) -> PullRequestHistory | None:
    if history is None:
        return None
    local = history.local
    filtered_local = local
    if local is not None:
        previous = [
            record
            for record in local.previous_findings
            if not paths_match_any(record.file, skipped, repository_paths=repository_paths)
        ]
        if previous != local.previous_findings:
            filtered_local = replace(local, previous_findings=previous)
    conversation = [
        event
        for event in history.conversation
        if not paths_match_any(event.file, skipped, repository_paths=repository_paths)
    ]
    if filtered_local is local and conversation == history.conversation:
        return history
    return replace(history, local=filtered_local, conversation=conversation)


def _filter_quality(
    results: list[ToolRunResult],
    skipped: set[str],
    repository_paths: set[str],
) -> list[ToolRunResult]:
    filtered: list[ToolRunResult] = []
    changed = False
    for result in results:
        diagnostics = tuple(
            diagnostic
            for diagnostic in result.diagnostics
            if not paths_match_any(diagnostic.file, skipped, repository_paths=repository_paths)
        )
        if diagnostics != result.diagnostics:
            changed = True
            filtered.append(replace(result, diagnostics=diagnostics))
        else:
            filtered.append(result)
    return filtered if changed else results
