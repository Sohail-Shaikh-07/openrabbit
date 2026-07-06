"""Implementation of the ``openrabbit index`` command.

Kept separate from ``cli.main`` so it can be unit-tested without going through
the Typer CLI runner.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from rag.embeddings import EmbeddingEngine
from rag.indexer import IndexResult, run_index
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QdrantHealthResult:
    """Qdrant connectivity check result."""

    ok: bool
    message: str
    collections: list[str]


def run_index_blocking(
    workspace: Path,
    qdrant_host: str = "localhost",
    qdrant_port: int = 6333,
) -> IndexResult:
    """Synchronous wrapper for the CLI to call from Typer command handlers.

    Parameters
    ----------
    workspace:
        The repository root to index. Must contain a ``.openrabbit/``
        directory (created by ``openrabbit init``).
    qdrant_host:
        Qdrant server host.
    qdrant_port:
        Qdrant server port.
    """
    store = VectorStore(host=qdrant_host, port=qdrant_port)
    engine = EmbeddingEngine()
    return asyncio.run(_async_index(workspace, store, engine))


def run_qdrant_health_check_blocking(
    qdrant_host: str = "localhost",
    qdrant_port: int = 6333,
    *,
    store: VectorStore | None = None,
) -> QdrantHealthResult:
    """Check whether Qdrant is reachable and list available collections."""
    vector_store = store or VectorStore(host=qdrant_host, port=qdrant_port)
    return asyncio.run(_async_health_check(vector_store))


async def _async_index(
    workspace: Path,
    store: VectorStore,
    engine: EmbeddingEngine,
) -> IndexResult:
    return await run_index(workspace, store=store, engine=engine)


async def _async_health_check(store: VectorStore) -> QdrantHealthResult:
    try:
        collections = sorted(await store.list_collections())
        return QdrantHealthResult(
            ok=True,
            message="Qdrant reachable",
            collections=collections,
        )
    except Exception as exc:
        return QdrantHealthResult(
            ok=False,
            message=f"Qdrant health check failed: {exc}",
            collections=[],
        )
    finally:
        await store.close()
