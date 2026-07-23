"""Deterministic relevance scoring for optional connector context."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from math import isfinite

from knowledge.connectors import KnowledgeConnectorRequest, KnowledgeItem, KnowledgeSourceKind

DEFAULT_CONNECTOR_RELEVANCE_THRESHOLD = 0.18

_ISSUE_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{2,}")
_PATH_SEPARATORS = re.compile(r"[\\/]+")


@dataclass(frozen=True)
class ConnectorRelevanceResult:
    """Connector items after deterministic scoring and filtering."""

    items: list[KnowledgeItem]
    candidate_items: int
    dropped_items: int
    dropped_reasons: dict[str, int]
    scores: dict[str, object]


def score_connector_items(
    request: KnowledgeConnectorRequest,
    items: list[KnowledgeItem],
    *,
    max_items: int,
    min_score: float = DEFAULT_CONNECTOR_RELEVANCE_THRESHOLD,
) -> ConnectorRelevanceResult:
    """Return connector items ranked by PR-specific deterministic relevance."""
    scored = [_score_item(request, item) for item in items]
    relevant = [item for item in scored if item.score is not None and item.score >= min_score]
    weak = max(0, len(scored) - len(relevant))
    ranked = sorted(
        relevant,
        key=lambda item: (
            -(item.score or 0.0),
            item.source_kind.value,
            item.source_id,
            item.title,
        ),
    )
    selected = ranked[:max_items]
    over_limit = max(0, len(ranked) - len(selected))
    dropped_reasons = {
        reason: count
        for reason, count in {
            "weak_connector_relevance": weak,
            "connector_item_limit": over_limit,
        }.items()
        if count > 0
    }
    return ConnectorRelevanceResult(
        items=selected,
        candidate_items=len(items),
        dropped_items=max(0, len(items) - len(selected)),
        dropped_reasons=dropped_reasons,
        scores=_score_summary(selected),
    )


def _score_item(request: KnowledgeConnectorRequest, item: KnowledgeItem) -> KnowledgeItem:
    item_text = _item_text(item)
    item_tokens = _tokens(item_text)
    request_tokens = _request_tokens(request)
    issue_overlap = _issue_keys(item_text).intersection(_issue_keys(_request_text(request)))
    path_matches = _path_matches(request.changed_paths, item)
    symbol_matches = _symbol_matches(request.changed_symbols, item_text)
    repo_match = bool(item.repo and _normalize_repo(item.repo) == _normalize_repo(request.repo))
    token_overlap = _token_overlap(request_tokens, item_tokens)

    base_score = _provider_score(item.score)
    score = base_score
    reasons: list[str] = []
    if item.score is not None:
        reasons.append("provider_score")
    if issue_overlap:
        score += 0.35
        reasons.append("issue_key")
    if path_matches:
        score += 0.30
        reasons.append("changed_path")
    if symbol_matches:
        score += 0.20
        reasons.append("changed_symbol")
    if repo_match:
        score += 0.10
        reasons.append("repo")
    if token_overlap > 0:
        score += min(0.20, token_overlap * 0.40)
        reasons.append("text_overlap")
    source_boost = _source_kind_boost(request, item.source_kind)
    if source_boost > 0:
        score += source_boost
        reasons.append("source_kind")

    normalized = round(min(score, 1.0), 4)
    if not reasons:
        reasons.append("no_pr_match")
    metadata = {
        **dict(item.metadata),
        "relevance_score": f"{normalized:.4f}",
        "relevance_reasons": ",".join(dict.fromkeys(reasons)),
    }
    if item.score is not None:
        metadata["provider_score"] = f"{item.score:.4f}"
    return replace(item, score=normalized, metadata=metadata)


def _provider_score(score: float | None) -> float:
    if score is None or not isfinite(score):
        return 0.0
    return min(max(score, 0.0), 1.0) * 0.45


def _source_kind_boost(
    request: KnowledgeConnectorRequest,
    source_kind: KnowledgeSourceKind,
) -> float:
    if source_kind is KnowledgeSourceKind.ISSUE_TRACKER and _issue_keys(_request_text(request)):
        return 0.05
    if source_kind is KnowledgeSourceKind.MULTI_REPO and request.changed_paths:
        return 0.05
    return 0.0


def _request_text(request: KnowledgeConnectorRequest) -> str:
    return " ".join(
        (
            request.repo,
            request.query,
            " ".join(request.changed_paths),
            " ".join(request.changed_symbols),
            " ".join(str(value) for value in request.metadata.values()),
        )
    )


def _item_text(item: KnowledgeItem) -> str:
    return " ".join(
        (
            item.source_id,
            item.title,
            item.body,
            item.url,
            item.repo,
            item.path,
            " ".join(str(value) for value in item.metadata.values()),
        )
    )


def _request_tokens(request: KnowledgeConnectorRequest) -> set[str]:
    return _tokens(_request_text(request))


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _issue_keys(text: str) -> set[str]:
    return {key.upper() for key in _ISSUE_KEY_RE.findall(text)}


def _path_matches(changed_paths: tuple[str, ...], item: KnowledgeItem) -> bool:
    if not changed_paths:
        return False
    item_paths = {
        _normalize_path(value)
        for value in (item.path, item.url, item.source_id, item.title, item.body)
        if value
    }
    item_text = " ".join(item_paths)
    for path in changed_paths:
        normalized = _normalize_path(path)
        if not normalized:
            continue
        basename = normalized.rsplit("/", 1)[-1]
        parent = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
        if normalized in item_text or (basename and basename in item_text):
            return True
        if parent and f"{parent}/" in item_text:
            return True
    return False


def _symbol_matches(changed_symbols: tuple[str, ...], text: str) -> bool:
    if not changed_symbols:
        return False
    lowered = text.lower()
    return any(symbol.lower() in lowered for symbol in changed_symbols if symbol)


def _token_overlap(request_tokens: set[str], item_tokens: set[str]) -> float:
    if not request_tokens or not item_tokens:
        return 0.0
    ignored = {"the", "and", "for", "with", "from", "this", "that", "context"}
    request_tokens = request_tokens - ignored
    item_tokens = item_tokens - ignored
    if not request_tokens or not item_tokens:
        return 0.0
    overlap = len(request_tokens.intersection(item_tokens))
    return overlap / max(1, min(len(request_tokens), len(item_tokens)))


def _normalize_path(value: str) -> str:
    return _PATH_SEPARATORS.sub("/", value.strip().lower()).strip("/")


def _normalize_repo(value: str) -> str:
    return value.strip().lower()


def _score_summary(items: list[KnowledgeItem]) -> dict[str, object]:
    scores = [item.score for item in items if item.score is not None]
    if not scores:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {
        "count": len(scores),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "avg": round(sum(scores) / len(scores), 4),
    }
