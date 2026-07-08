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
                for key in ("rule_source", "scope_path", "guideline_path"):
                    if key in payload:
                        row[key] = str(payload.get(key, ""))
                rows.append(row)
        return rows


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
                return RetrievalResult()
            query_vec = await self._build_query_vector(pr)
            changed_paths = _changed_paths_from_pr(pr)
            results = await asyncio.gather(
                self._fetch_security(query_vec, changed_paths),
                self._fetch_architecture(query_vec, changed_paths),
                self._fetch_performance(query_vec, changed_paths),
                self._fetch_tests(query_vec, changed_paths),
            )
            return RetrievalResult(
                security=results[0],
                architecture=results[1],
                performance=results[2],
                tests=results[3],
            )
        except Exception as exc:
            logger.warning(
                "RAG retrieval failed; continuing review with diff only: %s",
                exc,
            )
            logger.debug("RAG retrieval traceback", exc_info=True)
            return RetrievalResult()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _build_query_vector(self, pr: Any) -> Any:
        """Encode the PR context into a query embedding vector."""
        query_text = _query_text_from_pr(pr)
        query_chunk = Chunk(
            source_path=_QUERY_PATH,
            kind=ChunkKind.section,
            name="pr_query",
            text=query_text,
            language=None,
        )
        embedded = await self._engine.aencode([query_chunk])
        return embedded[0].vector

    async def _fetch_security(
        self, query_vec: Any, changed_paths: list[str]
    ) -> list[dict[str, Any]]:
        """Rules + source functions."""
        rules, funcs = await asyncio.gather(
            self._store.search(COLLECTION_RULES, query_vec, top_k=self._top_k),
            self._store.search(
                COLLECTION_FUNCTIONS,
                query_vec,
                top_k=self._top_k,
                filter=_path_filter(changed_paths),
            ),
        )
        return _dedup(rules + funcs)

    async def _fetch_architecture(
        self, query_vec: Any, changed_paths: list[str]
    ) -> list[dict[str, Any]]:
        """Docs + source functions."""
        docs, funcs = await asyncio.gather(
            self._store.search(COLLECTION_DOCS, query_vec, top_k=self._top_k),
            self._store.search(
                COLLECTION_FUNCTIONS,
                query_vec,
                top_k=self._top_k,
                filter=_path_filter(changed_paths),
            ),
        )
        return _dedup(docs + funcs)

    async def _fetch_performance(
        self, query_vec: Any, changed_paths: list[str]
    ) -> list[dict[str, Any]]:
        """Source functions only."""
        funcs = await self._store.search(
            COLLECTION_FUNCTIONS,
            query_vec,
            top_k=self._top_k,
            filter=_path_filter(changed_paths),
        )
        return _dedup(funcs)

    async def _fetch_tests(self, query_vec: Any, changed_paths: list[str]) -> list[dict[str, Any]]:
        """Reviews + source functions."""
        reviews, funcs = await asyncio.gather(
            self._store.search(COLLECTION_REVIEWS, query_vec, top_k=self._top_k),
            self._store.search(
                COLLECTION_FUNCTIONS,
                query_vec,
                top_k=self._top_k,
                filter=_path_filter(changed_paths),
            ),
        )
        return _dedup(reviews + funcs)


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

    When the same name appears more than once (e.g. from two collections),
    only the highest-scoring occurrence is kept.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for hit in sorted(hits, key=lambda h: h.get("score", 0.0), reverse=True):
        name = hit.get("payload", {}).get("name", "")
        if name and name in seen:
            continue
        if name:
            seen.add(name)
        out.append(hit)
    return out


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

    return " ".join(parts)


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


def _path_filter(changed_paths: list[str]) -> dict[str, Any] | None:
    if not changed_paths:
        return None
    return {
        "should": [
            {"key": "source_path", "match": {"value": path}}
            for path in dict.fromkeys(changed_paths)
        ]
    }


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
