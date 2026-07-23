"""Structured diagnostics for model-facing context selection."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from diff_summarization import summarize_large_low_risk_file
from knowledge.context_packing import DEFAULT_SOURCE_TOKEN_BUDGETS
from memory.history import PullRequestHistory, format_history_context
from quality.models import ToolRunResult
from rag.retriever import AgentDimension

_APPROX_CHARS_PER_TOKEN = 4


def build_context_precision_diagnostics(
    retrieval_result: Any | None,
    *,
    connector_context: Mapping[str, object] | None = None,
    pr_payload: Any | None = None,
    pr_history: Any | None = None,
    quality_results: list[Any] | None = None,
    command: str = "",
) -> dict[str, object]:
    """Return compact telemetry for context selected for a model-facing command."""
    hits_by_dimension = _hits_by_dimension(retrieval_result)
    selected_hits = _unique_hits(hit for hits in hits_by_dimension.values() for hit in hits)
    rag_hits = [hit for hit in selected_hits if not _is_connector_hit(hit)]
    connector_hits = [hit for hit in selected_hits if _is_connector_hit(hit)]
    retriever_stats = _retriever_stats(retrieval_result)
    connector_summary = _object_dict(connector_context)
    prompt_packing = _prompt_packing_summary(hits_by_dimension)

    candidate_items = _coerce_int(retriever_stats.get("candidate_items")) + _coerce_int(
        connector_summary.get("candidate_items")
    )
    if candidate_items <= 0:
        candidate_items = len(selected_hits)
    selected_items = len(selected_hits)
    dropped_items = _coerce_int(retriever_stats.get("dropped_items")) + _coerce_int(
        connector_summary.get("dropped_items")
    )
    auxiliary_sources = _auxiliary_source_summaries(
        pr_payload=pr_payload,
        pr_history=pr_history,
        quality_results=quality_results,
    )

    return {
        "schema_version": 1,
        "command": command,
        "candidate_items": candidate_items,
        "selected_items": selected_items,
        "dropped_items": dropped_items,
        "selected_sources": _source_counts(selected_hits),
        "selected_reasons": _reason_counts(selected_hits),
        "scores": _score_summary(selected_hits),
        "rag": {
            "candidate_items": _coerce_int(retriever_stats.get("candidate_items")) or len(rag_hits),
            "selected_items": len(rag_hits),
            "dropped_items": _coerce_int(retriever_stats.get("dropped_items")),
            "dropped_reasons": _object_dict(retriever_stats.get("dropped_reasons")),
            "selected_sources": _source_counts(rag_hits),
            "selected_reasons": _reason_counts(rag_hits),
            "scores": _score_summary(rag_hits),
            "dimensions": _dimension_summaries(hits_by_dimension),
        },
        "connectors": {
            "enabled": _coerce_int(connector_summary.get("enabled")),
            "available": _coerce_int(connector_summary.get("available")),
            "candidate_items": _coerce_int(connector_summary.get("candidate_items")),
            "selected_items": len(connector_hits),
            "dropped_items": _coerce_int(connector_summary.get("dropped_items")),
            "dropped_reasons": _object_dict(connector_summary.get("dropped_reasons")),
            "selected_sources": _source_counts(connector_hits),
            "configured_sources": _object_dict(connector_summary.get("sources")),
            "unavailable": len(_dict_list(connector_summary.get("unavailable"))),
            "failures": len(_dict_list(connector_summary.get("failures"))),
        },
        "source_budgets": dict(DEFAULT_SOURCE_TOKEN_BUDGETS),
        "source_packing": auxiliary_sources,
        "prompt_packing": prompt_packing,
    }


def _hits_by_dimension(retrieval_result: Any | None) -> dict[str, list[dict[str, Any]]]:
    if retrieval_result is None:
        return {dimension.value: [] for dimension in AgentDimension}
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for dimension in AgentDimension:
        value = getattr(retrieval_result, dimension.value, None)
        if isinstance(value, list):
            by_dimension[dimension.value] = [hit for hit in value if isinstance(hit, dict)]
        else:
            by_dimension[dimension.value] = []
    return by_dimension


def _retriever_stats(retrieval_result: Any | None) -> dict[str, object]:
    diagnostics = getattr(retrieval_result, "diagnostics", None)
    if not isinstance(diagnostics, dict):
        return {}
    return _object_dict(diagnostics.get("retriever"))


def _dimension_summaries(
    hits_by_dimension: Mapping[str, list[dict[str, Any]]],
) -> dict[str, dict[str, object]]:
    dimensions: dict[str, dict[str, object]] = {}
    for dimension, hits in hits_by_dimension.items():
        dimensions[dimension] = {
            "selected_items": len(hits),
            "selected_sources": _source_counts(hits),
            "selected_reasons": _reason_counts(hits),
            "scores": _score_summary(hits),
        }
    return dimensions


def _prompt_packing_summary(
    hits_by_dimension: Mapping[str, list[dict[str, Any]]],
) -> dict[str, object]:
    unique = _unique_hits(hit for hits in hits_by_dimension.values() for hit in hits)
    total_chars = sum(_payload_text_len(hit) for hit in unique)
    dimensions: dict[str, dict[str, int]] = {}
    for dimension, hits in hits_by_dimension.items():
        unique_dimension_hits = _unique_hits(hits)
        chars = sum(_payload_text_len(hit) for hit in unique_dimension_hits)
        dimensions[dimension] = {
            "items": len(unique_dimension_hits),
            "chars": chars,
            "estimated_tokens": _estimate_tokens(chars),
        }
    return {
        "context_items": len(unique),
        "context_chars": total_chars,
        "estimated_tokens": _estimate_tokens(total_chars),
        "dimensions": dimensions,
        "sources": _source_counts(unique),
    }


def _auxiliary_source_summaries(
    *,
    pr_payload: Any | None,
    pr_history: Any | None,
    quality_results: list[Any] | None,
) -> dict[str, dict[str, object]]:
    return {
        "changed_line_evidence": _changed_line_summary(pr_payload),
        "diff": _diff_summary(pr_payload),
        "memory": _memory_summary(pr_history),
        "linked_issue": _linked_issue_summary(pr_payload),
        "quality": _quality_summary(quality_results),
    }


def _unique_hits(hits: Any) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        key = _hit_key(hit)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def _hit_key(hit: dict[str, Any]) -> str:
    payload = _payload(hit)
    source = str(payload.get("source_path") or payload.get("path") or payload.get("url") or "")
    name = str(payload.get("name") or "")
    identifier = str(hit.get("id") or "")
    return "|".join((identifier, source, name))


def _source_counts(hits: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        source = _source_label(hit)
        if not source:
            continue
        counts[source] = counts.get(source, 0) + 1
    return counts


def _reason_counts(hits: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        reason = str(_payload(hit).get("retrieval_reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _score_summary(hits: list[dict[str, Any]]) -> dict[str, object]:
    scores = [float(score) for hit in hits if isinstance((score := hit.get("score")), int | float)]
    if not scores:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {
        "count": len(scores),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "avg": round(sum(scores) / len(scores), 4),
    }


def _source_label(hit: dict[str, Any]) -> str:
    payload = _payload(hit)
    connector = payload.get("connector")
    if connector:
        return str(connector)
    source = payload.get("source_path") or payload.get("path") or payload.get("url")
    return str(source or "")


def _payload_text_len(hit: dict[str, Any]) -> int:
    text = _payload(hit).get("text")
    return len(text) if isinstance(text, str) else 0


def _payload(hit: dict[str, Any]) -> dict[str, Any]:
    payload = hit.get("payload")
    return payload if isinstance(payload, dict) else {}


def _is_connector_hit(hit: dict[str, Any]) -> bool:
    payload = _payload(hit)
    return payload.get("kind") == "connector_context" or "connector" in payload


def _estimate_tokens(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, math.ceil(chars / _APPROX_CHARS_PER_TOKEN))


def _budget_summary(source: str, *, candidate_items: int, chars: int) -> dict[str, object]:
    budget = int(DEFAULT_SOURCE_TOKEN_BUDGETS[source])
    tokens = _estimate_tokens(chars)
    return {
        "max_tokens": budget,
        "candidate_items": candidate_items,
        "estimated_tokens": tokens,
        "over_budget": tokens > budget,
    }


def _changed_line_summary(pr_payload: Any | None) -> dict[str, object]:
    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list):
        return _budget_summary("changed_line_evidence", candidate_items=0, chars=0)
    lines = 0
    chars = 0
    for file_ in files:
        hunks = getattr(file_, "hunks", None)
        if not isinstance(hunks, list):
            continue
        for hunk in hunks:
            hunk_lines = getattr(hunk, "lines", None)
            if not isinstance(hunk_lines, list):
                continue
            for line in hunk_lines:
                if getattr(line, "kind", "") == "addition":
                    text = str(getattr(line, "text", "") or "")
                    lines += 1
                    chars += len(text)
    return _budget_summary("changed_line_evidence", candidate_items=lines, chars=chars)


def _diff_summary(pr_payload: Any | None) -> dict[str, object]:
    raw_diff = str(getattr(pr_payload, "diff", "") or "")
    files = getattr(pr_payload, "files", None)
    file_count = len(files) if isinstance(files, list) else 0
    hunk_count = 0
    chars = len(raw_diff)
    if isinstance(files, list):
        for file_ in files:
            hunks = getattr(file_, "hunks", None)
            if not isinstance(hunks, list):
                continue
            hunk_count += len(hunks)
            for hunk in hunks:
                for line in getattr(hunk, "lines", []) or []:
                    chars += len(str(getattr(line, "text", "") or ""))
    large_low_risk_summaries = []
    if isinstance(files, list):
        large_low_risk_summaries = [
            low_risk_summary
            for file_ in files
            if (low_risk_summary := summarize_large_low_risk_file(file_)) is not None
        ]
    summary = _budget_summary("diff", candidate_items=max(file_count, hunk_count), chars=chars)
    summary["files"] = file_count
    summary["hunks"] = hunk_count
    summary["large_low_risk_files"] = len(large_low_risk_summaries)
    summary["large_low_risk_changes"] = sum(item.changes for item in large_low_risk_summaries)
    summary["large_low_risk_diff_lines"] = sum(item.diff_lines for item in large_low_risk_summaries)
    return summary


def _memory_summary(pr_history: Any | None) -> dict[str, object]:
    if pr_history is None:
        return _budget_summary("memory", candidate_items=0, chars=0)
    try:
        text = format_history_context(pr_history)
    except Exception:
        text = ""
    previous = 0
    if isinstance(pr_history, PullRequestHistory) and pr_history.local is not None:
        previous = len(pr_history.local.previous_findings)
    events = getattr(pr_history, "conversation", [])
    learnings = getattr(pr_history, "learnings", [])
    candidate_items = previous
    candidate_items += len(events) if isinstance(events, list) else 0
    candidate_items += len(learnings) if isinstance(learnings, list) else 0
    return _budget_summary("memory", candidate_items=candidate_items, chars=len(text))


def _linked_issue_summary(pr_payload: Any | None) -> dict[str, object]:
    linked_issues = getattr(pr_payload, "linked_issues", None)
    if not isinstance(linked_issues, list):
        return _budget_summary("linked_issue", candidate_items=0, chars=0)
    chars = 0
    for issue in linked_issues:
        for attr in ("full_name", "title", "state", "body_preview", "url", "source"):
            chars += len(str(getattr(issue, attr, "") or ""))
    return _budget_summary("linked_issue", candidate_items=len(linked_issues), chars=chars)


def _quality_summary(quality_results: list[Any] | None) -> dict[str, object]:
    if not isinstance(quality_results, list):
        return _budget_summary("quality", candidate_items=0, chars=0)
    diagnostics = 0
    chars = 0
    for result in quality_results:
        if not isinstance(result, ToolRunResult):
            continue
        chars += len(result.summary)
        for diagnostic in result.diagnostics:
            diagnostics += 1
            chars += len(diagnostic.message) + len(diagnostic.file) + len(diagnostic.code)
    return _budget_summary("quality", candidate_items=diagnostics, chars=chars)


def _object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        {str(key): item for key, item in item.items()} for item in value if isinstance(item, dict)
    ]


def _coerce_int(value: object) -> int:
    if isinstance(value, int):
        return value
    return 0
