"""Qdrant vector-store wrapper for the OpenRabbit RAG pipeline.

Provides async upsert and search operations over named collections. Each
collection corresponds to a knowledge category (functions, classes, docs,
rules, reviews) as defined in the RAG design document.

The module wraps ``qdrant_client.AsyncQdrantClient`` and keeps the Qdrant API
surface narrow so that the rest of the pipeline interacts with typed Python
objects rather than raw Qdrant models.

``qdrant_client`` is imported lazily (on first use) so that the module can be
loaded even if Qdrant is not installed, and so that tests can inject a mock
client without triggering grpc/protobuf import side-effects.

Usage::

    from rag.vector_store import VectorStore, COLLECTION_FUNCTIONS

    store = VectorStore()
    await store.ensure_collection(COLLECTION_FUNCTIONS)
    await store.upsert(COLLECTION_FUNCTIONS, embedded_chunks)
    results = await store.search(COLLECTION_FUNCTIONS, query_vector, top_k=10)
    await store.close()
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import numpy as np

from rag.embeddings import EmbeddedChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VECTOR_SIZE: int = 384
"""Dimension of BGE-Small vectors (BAAI/bge-small-en-v1.5)."""

COLLECTION_FUNCTIONS = "functions"
COLLECTION_CLASSES = "classes"
COLLECTION_DOCS = "docs"
COLLECTION_RULES = "rules"
COLLECTION_REVIEWS = "reviews"

ALL_COLLECTIONS: tuple[str, ...] = (
    COLLECTION_FUNCTIONS,
    COLLECTION_CLASSES,
    COLLECTION_DOCS,
    COLLECTION_RULES,
    COLLECTION_REVIEWS,
)

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 6333


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------


class VectorStore:
    """Async wrapper around ``AsyncQdrantClient``.

    Parameters
    ----------
    host:
        Qdrant server host. Defaults to ``localhost``.
    port:
        Qdrant server port. Defaults to ``6333``.
    client:
        Optional pre-built async client. If provided, *host* and *port* are
        ignored. Useful for injecting a mock in tests.
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        client: Any = None,
    ) -> None:
        self._host = host
        self._port = port
        self._client: Any = client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ensure_collection(self, name: str, vector_size: int = VECTOR_SIZE) -> None:
        """Create *name* if it does not already exist on the server.

        Safe to call multiple times -- a second call for an existing collection
        is a no-op.
        """
        from qdrant_client.models import Distance, VectorParams

        client = self._get_client()
        result = await client.get_collections()
        existing = {c.name for c in result.collections}
        if name in existing:
            logger.debug("collection %r already exists", name)
            return

        logger.info("creating collection %r (dim=%d)", name, vector_size)
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE,
            ),
        )

    async def upsert(self, collection: str, items: list[EmbeddedChunk]) -> None:
        """Store *items* in *collection*.

        Each :class:`~rag.embeddings.EmbeddedChunk` becomes one Qdrant point
        with the chunk metadata stored as the point payload.
        """
        if not items:
            return

        from qdrant_client.models import PointStruct

        client = self._get_client()
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=item.vector.tolist(),
                payload=_chunk_payload(item),
            )
            for item in items
        ]

        logger.debug("upserting %d points into %r", len(points), collection)
        await client.upsert(
            collection_name=collection,
            points=points,
        )

    async def search(
        self,
        collection: str,
        query_vector: np.ndarray,
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to *top_k* nearest neighbours from *collection*.

        Parameters
        ----------
        collection:
            Which Qdrant collection to query.
        query_vector:
            The query embedding (same dimension as stored vectors).
        top_k:
            Maximum number of results to return.
        filter:
            Optional Qdrant filter dict. Passed as-is to
            ``AsyncQdrantClient.search``.

        Returns
        -------
        list[dict]
            Each element has ``id``, ``score``, and ``payload`` keys.
        """
        qdrant_filter = _build_filter(filter) if filter else None
        client = self._get_client()

        hits = await client.search(
            collection_name=collection,
            query_vector=query_vector.tolist(),
            limit=top_k,
            query_filter=qdrant_filter,
        )

        return [{"id": h.id, "score": h.score, "payload": h.payload} for h in hits]

    async def close(self) -> None:
        """Release resources held by the underlying Qdrant client."""
        if self._client is not None:
            await self._client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return (lazily creating) the async Qdrant client."""
        if self._client is None:
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(host=self._host, port=self._port)
        return self._client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_payload(item: EmbeddedChunk) -> dict[str, Any]:
    """Build the Qdrant point payload from an :class:`EmbeddedChunk`."""
    chunk = item.chunk
    start, end = chunk.byte_span
    return {
        "source_path": chunk.source_path.as_posix(),
        "kind": chunk.kind.value,
        "name": chunk.name,
        "text": chunk.text,
        "language": chunk.language,
        "byte_start": start,
        "byte_end": end,
    }


def _build_filter(filter_dict: dict[str, Any]) -> Any:
    """Convert a plain filter dict into a Qdrant ``Filter`` object."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    must_conditions = []
    for condition in filter_dict.get("must", []):
        key = condition["key"]
        match_value = condition["match"]["value"]
        must_conditions.append(FieldCondition(key=key, match=MatchValue(value=match_value)))
    return Filter(must=must_conditions) if must_conditions else None
