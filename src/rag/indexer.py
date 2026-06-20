"""Repository indexing pipeline for OpenRabbit.

Orchestrates the full RAG indexing pass:

1. Scan the repository with :class:`~rag.scanner.RepositoryScanner`.
2. Chunk each file with :class:`~rag.chunker.Chunker`.
3. Embed chunks in batches with :class:`~rag.embeddings.EmbeddingEngine`.
4. Ensure the required Qdrant collections exist.
5. Upsert all :class:`~rag.embeddings.EmbeddedChunk` objects into the
   collection that matches their knowledge category.

Usage (programmatic)::

    from rag.indexer import run_index

    result = await run_index(
        repo_root,
        store=VectorStore(),
        engine=EmbeddingEngine(),
    )
    print(f"Indexed {result.chunks_indexed} chunks from {result.files_scanned} files.")

Usage (CLI) is via ``openrabbit index``, which calls this function after
loading settings.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from rag.chunker import Chunk, Chunker, ChunkKind
from rag.embeddings import EmbeddedChunk, EmbeddingEngine
from rag.scanner import FileKind, RepositoryScanner
from rag.vector_store import (
    ALL_COLLECTIONS,
    COLLECTION_CLASSES,
    COLLECTION_DOCS,
    COLLECTION_FUNCTIONS,
    COLLECTION_REVIEWS,
    COLLECTION_RULES,
    VectorStore,
)

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 32


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexResult:
    """Summary returned by :func:`run_index`.

    Attributes
    ----------
    files_scanned:
        Total number of files the scanner visited (including those with no
        chunks, such as empty files).
    chunks_indexed:
        Total number of chunks successfully upserted into Qdrant.
    """

    files_scanned: int
    chunks_indexed: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_index(
    repo_root: Path,
    store: VectorStore,
    engine: EmbeddingEngine,
    batch_size: int = _EMBED_BATCH_SIZE,
) -> IndexResult:
    """Scan, chunk, embed, and store *repo_root* into Qdrant.

    Parameters
    ----------
    repo_root:
        Root directory of the repository to index.
    store:
        Qdrant wrapper. Collections are created lazily as needed.
    engine:
        Embedding engine used to encode chunk texts.
    batch_size:
        Number of chunks encoded in a single embedding call. Larger values
        are faster but consume more memory.

    Returns
    -------
    IndexResult
        Summary of the indexing pass.
    """
    await _ensure_collections(store)

    scanner = RepositoryScanner()
    chunker = Chunker()

    files_scanned = 0
    chunks_indexed = 0

    pending: list[tuple[str, Chunk]] = []  # (collection_name, chunk)

    for record in scanner.scan(repo_root):
        files_scanned += 1
        file_chunks = chunker.chunk(record)
        if not file_chunks:
            continue
        collection = _collection_for(record.kind, file_chunks[0].kind)
        for chunk in file_chunks:
            pending.append((collection, chunk))

        if len(pending) >= batch_size:
            chunks_indexed += await _flush(pending, store, engine)
            pending.clear()

    if pending:
        chunks_indexed += await _flush(pending, store, engine)

    logger.info(
        "Indexing complete: %d files scanned, %d chunks stored",
        files_scanned,
        chunks_indexed,
    )
    return IndexResult(files_scanned=files_scanned, chunks_indexed=chunks_indexed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _ensure_collections(store: VectorStore) -> None:
    await asyncio.gather(*[store.ensure_collection(name) for name in ALL_COLLECTIONS])


async def _flush(
    pending: list[tuple[str, Chunk]],
    store: VectorStore,
    engine: EmbeddingEngine,
) -> int:
    """Embed and upsert one batch. Returns the number of chunks stored."""
    chunks = [c for _, c in pending]
    embedded: list[EmbeddedChunk] = await engine.aencode(chunks)

    by_collection: dict[str, list[EmbeddedChunk]] = {}
    for (collection, _), ec in zip(pending, embedded, strict=False):
        by_collection.setdefault(collection, []).append(ec)

    await asyncio.gather(
        *[store.upsert(collection=col, items=items) for col, items in by_collection.items()]
    )
    return len(embedded)


def _collection_for(file_kind: FileKind, chunk_kind: ChunkKind) -> str:
    """Map file + chunk kind to the correct Qdrant collection."""
    if file_kind is FileKind.rules:
        return COLLECTION_RULES
    if file_kind is FileKind.documentation:
        return COLLECTION_DOCS
    if chunk_kind is ChunkKind.function:
        return COLLECTION_FUNCTIONS
    if chunk_kind is ChunkKind.klass:
        return COLLECTION_CLASSES
    if chunk_kind is ChunkKind.section:
        return COLLECTION_DOCS
    return COLLECTION_REVIEWS
