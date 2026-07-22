"""Structured diagnostics for model-facing context selection."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from rag.retriever import AgentDimension

_APPROX_CHARS_PER_TOKEN = 4


def build_context_precision_diagnostics(
    retrieval_result: Any | None,
    *,
    connector_context: Mapping[str, object] | None = None,
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
