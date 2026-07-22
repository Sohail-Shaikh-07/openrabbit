"""Context retrieval layer for the OpenRabbit RAG pipeline.

Translates a :class:`~github_.pr.PullRequestPayload` into per-agent context
packages by querying Qdrant for relevant chunks from multiple collections.

Four agent dimensions are supported (matching the agent layer in Phase 4):

* **security** -- rules + source functions
* **architecture** -- docs + source functions
* **performance** -- source functions
* **tests** -- reviews + source functions

Each dimension runs its own set of Qdrant searches concurrently so that total
retrieval latency is bounded by the slowest single search, not the sum.

If Qdrant is unreachable, :meth:`ContextRetriever.retrieve` returns an empty
:class:`RetrievalResult` so that the review pipeline can continue with the PR
diff alone rather than failing completely.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from rag.chunker import Chunk, ChunkKind
from rag.embeddings import EmbeddingEngine
from rag.vector_store import (
    COLLECTION_DOCS,
    COLLECTION_FUNCTIONS,
    COLLECTION_REVIEWS,
    COLLECTION_RULES,
    VectorStore,
)

logger = logging.getLogger(__name__)

_TOP_K = 10
_RELATED_FUNCTION_TOP_K = 5
_RETRIEVAL_COLLECTIONS = (
    COLLECTION_DOCS,
    COLLECTION_FUNCTIONS,
    COLLECTION_REVIEWS,
    COLLECTION_RULES,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class AgentDimension(StrEnum):
    """The four review agent types that receive RAG context."""

    security = "security"
    architecture = "architecture"
    performance = "performance"
    tests = "tests"


@dataclass
class RetrievalResult:
    """Per-dimension lists of Qdrant search hits.

    Each list element is a dict with ``id``, ``score``, and ``payload`` keys.
    """

    security: list[dict[str, Any]] = field(default_factory=list)
    architecture: list[dict[str, Any]] = field(default_factory=list)
    performance: list[dict[str, Any]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        """Return the result as a plain dict keyed by dimension name."""
        return {
            AgentDimension.security: self.security,
            AgentDimension.architecture: self.architecture,
            AgentDimension.performance: self.performance,
            AgentDimension.tests: self.tests,
        }

    def provenance(self) -> list[dict[str, Any]]:
        """Return compact source provenance for retrieved context."""
        rows: list[dict[str, Any]] = []
        for dimension, hits in self.as_dict().items():
            for hit in hits:
                payload = hit.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}
                row = {
                    "dimension": str(dimension),
                    "source_path": str(payload.get("source_path", "")),
                    "name": str(payload.get("name", "")),
                    "kind": str(payload.get("kind", "")),
                    "score": hit.get("score"),
                }
                for key in (
                    "rule_source",
                    "scope_path",
                    "guideline_path",
                    "connector",
                    "connector_source_kind",
                    "source_id",
                    "url",
                    "repo",
                    "path",
                ):
                    if key in payload:
                        row[key] = str(payload.get(key, ""))
                if "retrieval_reason" in payload:
                    row["retrieval_reason"] = str(payload.get("retrieval_reason", ""))
                rows.append(row)
        return rows


@dataclass(frozen=True)
class RetrievalPlan:
    """Compact retrieval hints derived from a pull request."""

    query_text: str
    changed_paths: tuple[str, ...]
    changed_symbols: tuple[str, ...]
    changed_dirs: tuple[str, ...]
    related_test_paths: tuple[str, ...]
    guideline_scopes: tuple[str, ...]
    architecture_paths: tuple[str, ...]


# ---------------------------------------------------------------------------
# ContextRetriever
# ---------------------------------------------------------------------------


class ContextRetriever:
    """Retrieves relevant context for each review agent dimension.

    Parameters
    ----------
    engine:
        Embedding engine used to encode the query derived from the PR.
    store:
        Vector store providing the Qdrant search interface.
    top_k:
        Maximum number of results per collection query. Defaults to 10.
    """

    def __init__(
        self,
        engine: EmbeddingEngine,
        store: VectorStore,
        top_k: int = _TOP_K,
    ) -> None:
        self._engine = engine
        self._store = store
        self._top_k = top_k

    async def retrieve(self, pr: Any) -> RetrievalResult:
        """Return a :class:`RetrievalResult` for all four agent dimensions.

        Parameters
        ----------
        pr:
            A :class:`~github_.pr.PullRequestPayload` whose changed files and
            title form the retrieval query.

        Returns
        -------
        RetrievalResult
            Populated from Qdrant. If the store is unreachable, all lists are
            empty and a warning is logged.
        """
        try:
            if not await self._store.has_any_collection(_RETRIEVAL_COLLECTIONS):
                logger.info(
                    "RAG index is unavailable; continuing review with diff only",
                )
                return RetrievalResult(
                    diagnostics={
                        "retriever": {
                            "available": False,
                            "candidate_items": 0,
                            "selected_items": 0,
                            "dropped_items": 0,
                            "dropped_reasons": {"rag_index_unavailable": 1},
                            "dimensions": {},
                        }
                    }
                )
            plan = _retrieval_plan_from_pr(pr)
            query_vec = await self._build_query_vector(plan.query_text)
            results = await asyncio.gather(
                self._fetch_rule_context(query_vec, plan),
                self._fetch_doc_context(query_vec, plan),
                self._store.search(COLLECTION_REVIEWS, query_vec, top_k=self._top_k),
                self._fetch_function_context(query_vec, plan),
            )
            rules, docs, reviews, funcs = results
            security_candidates = rules + funcs
            architecture_candidates = docs + funcs
            performance_candidates = funcs
            tests_candidates = reviews + funcs
            security = _pack_hits(security_candidates, plan, limit=self._top_k)
            architecture = _pack_hits(architecture_candidates, plan, limit=self._top_k)
            performance = _pack_hits(performance_candidates, plan, limit=self._top_k)
            tests = _pack_hits(tests_candidates, plan, limit=self._top_k)
            diagnostics = _retrieval_diagnostics(
                {
                    AgentDimension.security.value: (security_candidates, security),
                    AgentDimension.architecture.value: (architecture_candidates, architecture),
                    AgentDimension.performance.value: (performance_candidates, performance),
                    AgentDimension.tests.value: (tests_candidates, tests),
                },
                limit=self._top_k,
            )
            return RetrievalResult(
                security=security,
                architecture=architecture,
                performance=performance,
                tests=tests,
                diagnostics={"retriever": diagnostics},
            )
        except Exception as exc:
            logger.warning(
                "RAG retrieval failed; continuing review with diff only: %s",
                exc,
            )
            logger.debug("RAG retrieval traceback", exc_info=True)
            return RetrievalResult(
                diagnostics={
                    "retriever": {
                        "available": False,
                        "candidate_items": 0,
                        "selected_items": 0,
                        "dropped_items": 0,
                        "dropped_reasons": {"retrieval_failed": 1},
                        "dimensions": {},
                    }
                }
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _build_query_vector(self, query_text: str) -> Any:
        """Encode the PR context into a query embedding vector."""
        query_chunk = Chunk(
            source_path=_QUERY_PATH,
            kind=ChunkKind.section,
            name="pr_query",
            text=query_text,
            language=None,
        )
        embedded = await self._engine.aencode([query_chunk])
        return embedded[0].vector

    async def _fetch_function_context(
        self, query_vec: Any, plan: RetrievalPlan
    ) -> list[dict[str, Any]]:
        """Fetch changed-file, changed-symbol, and semantic function context."""
        searches = [
            self._store.search(
                COLLECTION_FUNCTIONS,
                query_vec,
                top_k=_RELATED_FUNCTION_TOP_K,
            )
        ]
        path_filter = _path_filter(plan.changed_paths)
        if path_filter:
            searches.append(
                self._store.search(
                    COLLECTION_FUNCTIONS,
                    query_vec,
                    top_k=self._top_k,
                    filter=path_filter,
                )
            )
        symbol_filter = _symbol_filter(plan.changed_symbols)
        if symbol_filter:
            searches.append(
                self._store.search(
                    COLLECTION_FUNCTIONS,
                    query_vec,
                    top_k=self._top_k,
                    filter=symbol_filter,
                )
            )

        results = await asyncio.gather(*searches)
        return [hit for result in results for hit in result]

    async def _fetch_rule_context(
        self, query_vec: Any, plan: RetrievalPlan
    ) -> list[dict[str, Any]]:
        """Fetch repository rules, preferring scopes touched by the PR."""
        searches = [
            self._store.search(COLLECTION_RULES, query_vec, top_k=self._top_k),
        ]
        scope_filter = _scope_filter(plan.guideline_scopes)
        if scope_filter:
            searches.append(
                self._store.search(
                    COLLECTION_RULES,
                    query_vec,
                    top_k=self._top_k,
                    filter=scope_filter,
                )
            )
        results = await asyncio.gather(*searches)
        return [hit for result in results for hit in result]

    async def _fetch_doc_context(self, query_vec: Any, plan: RetrievalPlan) -> list[dict[str, Any]]:
        """Fetch documentation, preferring known architecture document paths."""
        searches = [
            self._store.search(COLLECTION_DOCS, query_vec, top_k=self._top_k),
        ]
        architecture_filter = _source_path_filter(plan.architecture_paths)
        if architecture_filter:
            searches.append(
                self._store.search(
                    COLLECTION_DOCS,
                    query_vec,
                    top_k=self._top_k,
                    filter=architecture_filter,
                )
            )
        results = await asyncio.gather(*searches)
        return [hit for result in results for hit in result]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_QUERY_PATH = _dummy_path = type(
    "_",
    (),
    {"as_posix": lambda _: "query", "__str__": lambda _: "query"},
)()


def _dedup(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate hits based on the payload ``name`` field.

    When the same name appears more than once, the first hit in the incoming
    order is kept. Callers sort by relevance before calling this helper.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for hit in hits:
        name = hit.get("payload", {}).get("name", "")
        if name and name in seen:
            continue
        if name:
            seen.add(name)
        out.append(hit)
    return out


def _pack_hits(
    hits: list[dict[str, Any]],
    plan: RetrievalPlan,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Deduplicate, rank, annotate, and cap retrieved context."""
    packed: list[dict[str, Any]] = []
    for hit in hits:
        reason = _retrieval_reason(hit.get("payload", {}), plan)
        packed.append(_annotate_hit(hit, reason))

    ranked = sorted(
        packed,
        key=lambda hit: (
            _reason_priority(hit),
            float(hit.get("score", 0.0) or 0.0),
            str(hit.get("payload", {}).get("source_path", "")),
            str(hit.get("payload", {}).get("name", "")),
        ),
        reverse=True,
    )
    return _dedup(ranked)[:limit]


def _retrieval_diagnostics(
    dimensions: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    *,
    limit: int,
) -> dict[str, Any]:
    """Return compact retrieval counts for eval and troubleshooting output."""
    dimension_stats: dict[str, dict[str, Any]] = {}
    dropped_reasons: dict[str, int] = {}
    candidate_total = 0
    selected_total = 0
    for dimension, (candidates, selected) in dimensions.items():
        unique_candidates = _dedup([hit for hit in candidates if isinstance(hit, dict)])
        deduplicated = max(0, len(candidates) - len(unique_candidates))
        over_limit = max(0, len(unique_candidates) - limit)
        selected_count = len(selected)
        candidate_count = len(candidates)
        candidate_total += candidate_count
        selected_total += selected_count
        for reason, count in (
            ("deduplicated", deduplicated),
            ("top_k_limit", over_limit),
        ):
            if count > 0:
                dropped_reasons[reason] = dropped_reasons.get(reason, 0) + count
        dimension_stats[dimension] = {
            "candidate_items": candidate_count,
            "selected_items": selected_count,
            "dropped_items": max(0, candidate_count - selected_count),
            "dropped_reasons": {
                reason: count
                for reason, count in {
                    "deduplicated": deduplicated,
                    "top_k_limit": over_limit,
                }.items()
                if count > 0
            },
            "selected_reasons": _selected_reason_counts(selected),
        }
    return {
        "available": True,
        "candidate_items": candidate_total,
        "selected_items": selected_total,
        "dropped_items": max(0, candidate_total - selected_total),
        "dropped_reasons": dropped_reasons,
        "dimensions": dimension_stats,
    }


def _selected_reason_counts(hits: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        payload = hit.get("payload", {})
        if not isinstance(payload, dict):
            continue
        reason = str(payload.get("retrieval_reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _annotate_hit(hit: dict[str, Any], reason: str) -> dict[str, Any]:
    payload = hit.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    annotated = dict(hit)
    annotated["payload"] = {**payload, "retrieval_reason": reason}
    return annotated


def _reason_priority(hit: dict[str, Any]) -> int:
    payload = hit.get("payload", {})
    if not isinstance(payload, dict):
        return 0
    reason = str(payload.get("retrieval_reason", "semantic"))
    return {
        "changed_file": 100,
        "scoped_guideline": 95,
        "repository_guideline": 90,
        "architecture_doc": 88,
        "changed_symbol": 85,
        "related_test": 82,
        "nearby_path": 75,
        "semantic": 50,
    }.get(reason, 0)


def _retrieval_reason(payload: Any, plan: RetrievalPlan) -> str:
    if not isinstance(payload, dict):
        return "semantic"
    source_path = str(payload.get("source_path", "") or "")
    name = str(payload.get("name", "") or "")
    scope_path = str(payload.get("scope_path", "") or "")

    if source_path and source_path in plan.changed_paths:
        return "changed_file"
    if payload.get("rule_source") == "repository_guideline":
        if scope_path and _scope_applies(scope_path, plan.changed_paths):
            return "scoped_guideline"
        return "repository_guideline"
    if source_path and source_path in plan.architecture_paths:
        return "architecture_doc"
    if name and name in plan.changed_symbols:
        return "changed_symbol"
    if source_path and source_path in plan.related_test_paths:
        return "related_test"
    if _is_near_changed_path(source_path, plan.changed_dirs):
        return "nearby_path"
    return "semantic"


def _query_text_from_pr(pr: Any) -> str:
    """Build retrieval text from PR metadata, changed files, and hunk lines."""
    parts: list[str] = []
    pull_request = getattr(pr, "pull_request", None)
    title = str(getattr(pull_request, "title", "") or "").strip()
    body = str(getattr(pull_request, "body", "") or "").strip()
    if title:
        parts.append(title)
    if body:
        parts.append(body)

    files = getattr(pr, "files", None)
    symbols: set[str] = set()
    if isinstance(files, list):
        for file_ in files:
            path = str(getattr(file_, "path", "") or "").strip()
            if path:
                parts.append(path)
            hunks = getattr(file_, "hunks", None)
            if not isinstance(hunks, list):
                continue
            for hunk in hunks:
                for line in getattr(hunk, "lines", []) or []:
                    text = str(getattr(line, "text", "") or "").strip()
                    if text:
                        parts.append(text)
                        symbols.update(_symbols_from_line(text))

    if symbols:
        parts.append("changed symbols: " + " ".join(sorted(symbols)))
    paths = tuple(dict.fromkeys(_changed_paths_from_pr(pr)))
    dirs = tuple(dict.fromkeys(_directory_hints(paths)))
    related_tests = _related_test_paths(paths)
    if dirs:
        parts.append("nearby directories: " + " ".join(dirs))
    if related_tests:
        parts.append("related tests: " + " ".join(related_tests))
    if paths:
        parts.append("architecture context: architecture docs adr design decisions")

    return " ".join(parts)


def _retrieval_plan_from_pr(pr: Any) -> RetrievalPlan:
    changed_paths = tuple(dict.fromkeys(_changed_paths_from_pr(pr)))
    changed_symbols = tuple(sorted(_changed_symbols_from_pr(pr)))
    changed_dirs = tuple(dict.fromkeys(_directory_hints(changed_paths)))
    related_test_paths = _related_test_paths(changed_paths)
    guideline_scopes = _guideline_scope_hints(changed_paths)
    architecture_paths = _architecture_doc_paths(changed_paths)
    return RetrievalPlan(
        query_text=_query_text_from_pr(pr),
        changed_paths=changed_paths,
        changed_symbols=changed_symbols,
        changed_dirs=changed_dirs,
        related_test_paths=related_test_paths,
        guideline_scopes=guideline_scopes,
        architecture_paths=architecture_paths,
    )


def _changed_paths_from_pr(pr: Any) -> list[str]:
    files = getattr(pr, "files", None)
    if not isinstance(files, list):
        return []
    paths = []
    for file_ in files:
        path = str(getattr(file_, "path", "") or "").strip()
        if path:
            paths.append(path)
    return paths


def _changed_symbols_from_pr(pr: Any) -> set[str]:
    files = getattr(pr, "files", None)
    if not isinstance(files, list):
        return set()
    symbols: set[str] = set()
    for file_ in files:
        hunks = getattr(file_, "hunks", None)
        if not isinstance(hunks, list):
            continue
        for hunk in hunks:
            for line in getattr(hunk, "lines", []) or []:
                text = str(getattr(line, "text", "") or "")
                symbols.update(_symbols_from_line(text))
    return symbols


def _directory_hints(changed_paths: tuple[str, ...]) -> list[str]:
    dirs: list[str] = []
    for path in changed_paths:
        parent = str(PurePosixPath(path).parent)
        if parent and parent != ".":
            dirs.append(parent)
    return dirs


def _path_filter(changed_paths: tuple[str, ...]) -> dict[str, Any] | None:
    paths = tuple(dict.fromkeys((*changed_paths, *_related_test_paths(changed_paths))))
    return _source_path_filter(paths)


def _source_path_filter(paths: tuple[str, ...]) -> dict[str, Any] | None:
    if not paths:
        return None
    return {
        "should": [
            {"key": "source_path", "match": {"value": path}} for path in dict.fromkeys(paths)
        ]
    }


def _symbol_filter(changed_symbols: tuple[str, ...]) -> dict[str, Any] | None:
    if not changed_symbols:
        return None
    return {
        "should": [
            {"key": "name", "match": {"value": symbol}} for symbol in dict.fromkeys(changed_symbols)
        ]
    }


def _scope_filter(scopes: tuple[str, ...]) -> dict[str, Any] | None:
    if not scopes:
        return None
    return {
        "should": [
            {"key": "scope_path", "match": {"value": scope}} for scope in dict.fromkeys(scopes)
        ]
    }


def _scope_applies(scope_path: str, changed_paths: tuple[str, ...]) -> bool:
    if not scope_path:
        return True
    prefix = f"{scope_path.rstrip('/')}/"
    return any(path == scope_path or path.startswith(prefix) for path in changed_paths)


def _is_near_changed_path(source_path: str, changed_dirs: tuple[str, ...]) -> bool:
    if not source_path or not changed_dirs:
        return False
    return any(source_path.startswith(f"{path.rstrip('/')}/") for path in changed_dirs)


def _related_test_paths(changed_paths: tuple[str, ...]) -> tuple[str, ...]:
    paths: list[str] = []
    for path in changed_paths:
        if _is_test_source_path(path):
            paths.append(path)
            continue
        pure = PurePosixPath(path)
        suffix = pure.suffix
        stem = pure.stem
        if not suffix:
            continue
        parts = pure.parts
        without_src = PurePosixPath(*parts[1:]) if parts and parts[0] in {"src", "app"} else pure
        paths.extend(
            (
                _join_posix("tests", without_src.parent.as_posix(), f"test_{stem}{suffix}"),
                f"tests/test_{stem}{suffix}",
                _join_posix("test", without_src.parent.as_posix(), f"test_{stem}{suffix}"),
            )
        )
        if suffix in {".ts", ".tsx", ".js", ".jsx"}:
            base = pure.with_suffix("").as_posix()
            paths.extend((f"{base}.test{suffix}", f"{base}.spec{suffix}"))
    return tuple(dict.fromkeys(_clean_posix_path(path) for path in paths if path))


def _guideline_scope_hints(changed_paths: tuple[str, ...]) -> tuple[str, ...]:
    scopes = ["."]
    for path in changed_paths:
        parent = PurePosixPath(path).parent
        if str(parent) == ".":
            continue
        parts = parent.parts
        for index in range(1, len(parts) + 1):
            scopes.append(PurePosixPath(*parts[:index]).as_posix())
    return tuple(dict.fromkeys(scopes))


def _architecture_doc_paths(changed_paths: tuple[str, ...]) -> tuple[str, ...]:
    paths = [
        "architecture.md",
        "ARCHITECTURE.md",
        "docs/architecture.md",
        "docs/ARCHITECTURE.md",
        "docs/adr.md",
        "docs/ADR.md",
        ".openrabbit/architecture.md",
    ]
    for path in changed_paths:
        parent = PurePosixPath(path).parent
        if str(parent) == ".":
            continue
        paths.extend(
            (
                f"docs/{parent.as_posix()}/architecture.md",
                f"docs/{parent.as_posix()}/adr.md",
            )
        )
    return tuple(dict.fromkeys(paths))


def _is_test_source_path(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = pure.parts
    name = pure.name
    return (
        any(part in {"test", "tests"} for part in parts)
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
        or name.endswith((".test.js", ".spec.js"))
    )


def _join_posix(*parts: str) -> str:
    return _clean_posix_path("/".join(part for part in parts if part and part != "."))


def _clean_posix_path(path: str) -> str:
    while "/./" in path:
        path = path.replace("/./", "/")
    return path.replace("//", "/").strip("/")


_PY_SYMBOL_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)


def _symbols_from_line(text: str) -> set[str]:
    symbols: set[str] = set()
    for pattern in (_PY_SYMBOL_RE, _JS_SYMBOL_RE):
        match = pattern.match(text)
        if match:
            symbols.add(match.group(1))
    return symbols
