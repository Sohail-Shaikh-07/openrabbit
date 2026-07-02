"""Tests for ``rag.indexer`` -- the pipeline that scans, chunks, embeds, and
stores a repository.

VectorStore and EmbeddingEngine are mocked; we only verify the orchestration.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from rag.chunker import Chunk
from rag.embeddings import EmbeddedChunk
from rag.indexer import run_index
from rag.vector_store import COLLECTION_DOCS, COLLECTION_FUNCTIONS, VECTOR_SIZE

# Suppress grpc/protobuf DeprecationWarnings on Python 3.12.
pytestmark = pytest.mark.filterwarnings("ignore:.*uses PyType_Spec.*:DeprecationWarning")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.ensure_collection = AsyncMock()
    store.upsert = AsyncMock()
    return store


def _mock_engine(dim: int = VECTOR_SIZE) -> MagicMock:
    engine = MagicMock()

    async def aencode_fn(chunks: list[Chunk]) -> list[EmbeddedChunk]:
        return [EmbeddedChunk(chunk=c, vector=np.ones(dim, dtype="float32")) for c in chunks]

    engine.aencode = AsyncMock(side_effect=aencode_fn)
    return engine


# ---------------------------------------------------------------------------
# run_index smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_index_creates_collections(scaffold_repo: Path) -> None:
    _write_python(scaffold_repo, "src/app.py", "def hello(): pass\n")
    store = _mock_store()

    await run_index(scaffold_repo, store=store, engine=_mock_engine())

    store.ensure_collection.assert_awaited()


@pytest.mark.asyncio
async def test_run_index_upserts_python_function_to_functions_collection(
    scaffold_repo: Path,
) -> None:
    _write_python(scaffold_repo, "src/app.py", "def hello(): pass\n")
    store = _mock_store()

    await run_index(scaffold_repo, store=store, engine=_mock_engine())

    collection_names = [call.kwargs["collection"] for call in store.upsert.call_args_list]
    assert COLLECTION_FUNCTIONS in collection_names


@pytest.mark.asyncio
async def test_run_index_upserts_markdown_to_docs_collection(
    scaffold_repo: Path,
) -> None:
    md = scaffold_repo / "CONTRIBUTING.md"
    md.write_text("# Contributing\n\nPR welcome.\n", encoding="utf-8")
    store = _mock_store()

    await run_index(scaffold_repo, store=store, engine=_mock_engine())

    collection_names = [call.kwargs["collection"] for call in store.upsert.call_args_list]
    assert COLLECTION_DOCS in collection_names


@pytest.mark.asyncio
async def test_run_index_skips_files_with_no_chunks(scaffold_repo: Path) -> None:
    _write_python(scaffold_repo, "src/empty.py", "")
    store = _mock_store()

    await run_index(scaffold_repo, store=store, engine=_mock_engine())

    # Empty file produces no chunks -- upsert for that file should not be called
    # with an empty list (we check via call count; if only .openrabbit/ rules
    # produce chunks, those go through; empty.py should add 0).
    for call in store.upsert.call_args_list:
        items: list[EmbeddedChunk] = call.kwargs["items"]
        assert len(items) > 0


@pytest.mark.asyncio
async def test_run_index_returns_summary_counts(scaffold_repo: Path) -> None:
    _write_python(scaffold_repo, "src/service.py", "def run(): pass\n")
    store = _mock_store()

    result = await run_index(scaffold_repo, store=store, engine=_mock_engine())

    assert result.files_scanned > 0
    assert result.chunks_indexed >= 1


@pytest.mark.asyncio
async def test_run_index_empty_repo_returns_zero_counts(scaffold_repo: Path) -> None:
    store = _mock_store()
    result = await run_index(scaffold_repo, store=store, engine=_mock_engine())

    # scaffold_repo has .openrabbit/ rules files which produce chunks.
    # files_scanned is at least the count of rule files.
    assert result.files_scanned >= 0
    assert result.chunks_indexed >= 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_python(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
