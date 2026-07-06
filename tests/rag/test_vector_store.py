"""Tests for ``rag.vector_store``.

The qdrant-client is mocked so tests run without a live Qdrant instance.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from rag.chunker import Chunk, ChunkKind
from rag.embeddings import EmbeddedChunk
from rag.vector_store import (
    COLLECTION_DOCS,
    COLLECTION_FUNCTIONS,
    COLLECTION_RULES,
    VECTOR_SIZE,
    VectorStore,
)

# google.protobuf 3.x emits DeprecationWarnings about PyType_Spec when its
# C extension is imported on Python 3.12. Suppress these for this module so
# the strict project-wide "error::DeprecationWarning" filter does not turn
# qdrant_client model imports into test failures.
pytestmark = pytest.mark.filterwarnings("ignore:.*uses PyType_Spec.*:DeprecationWarning")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _embedded(name: str = "foo", kind: ChunkKind = ChunkKind.function) -> EmbeddedChunk:
    chunk = Chunk(
        source_path=Path("src/foo.py"),
        kind=kind,
        name=name,
        text=f"def {name}(): pass",
        language="python",
        byte_span=(0, 20),
    )
    return EmbeddedChunk(chunk=chunk, vector=np.ones(VECTOR_SIZE, dtype="float32"))


def _async_qdrant_mock() -> MagicMock:
    """Return a mock AsyncQdrantClient with the methods VectorStore uses."""
    client = MagicMock()
    client.get_collections = AsyncMock(return_value=MagicMock(collections=[]))
    client.create_collection = AsyncMock(return_value=True)
    client.upsert = AsyncMock(return_value=MagicMock(status="completed"))
    client.search = AsyncMock(return_value=[])
    client.query_points = AsyncMock(return_value=MagicMock(points=[]))
    client.close = AsyncMock()
    return client


def _collection(name: str) -> MagicMock:
    collection = MagicMock()
    collection.name = name
    return collection


# ---------------------------------------------------------------------------
# VectorStore.ensure_collection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_collection_creates_if_missing() -> None:
    mock_client = _async_qdrant_mock()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    store = VectorStore(client=mock_client)

    await store.ensure_collection(COLLECTION_FUNCTIONS)

    mock_client.create_collection.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_collection_skips_if_exists() -> None:
    mock_client = _async_qdrant_mock()
    existing = MagicMock()
    existing.name = COLLECTION_FUNCTIONS
    mock_client.get_collections.return_value = MagicMock(collections=[existing])
    store = VectorStore(client=mock_client)

    await store.ensure_collection(COLLECTION_FUNCTIONS)

    mock_client.create_collection.assert_not_awaited()


# ---------------------------------------------------------------------------
# VectorStore.upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_calls_qdrant_upsert() -> None:
    mock_client = _async_qdrant_mock()
    store = VectorStore(client=mock_client)
    items = [_embedded("alpha"), _embedded("beta")]

    await store.upsert(COLLECTION_FUNCTIONS, items)

    mock_client.upsert.assert_awaited_once()
    call_args = mock_client.upsert.call_args
    assert call_args.kwargs["collection_name"] == COLLECTION_FUNCTIONS
    points = call_args.kwargs["points"]
    assert len(points) == 2


@pytest.mark.asyncio
async def test_upsert_empty_list_does_not_call_qdrant() -> None:
    mock_client = _async_qdrant_mock()
    store = VectorStore(client=mock_client)

    await store.upsert(COLLECTION_FUNCTIONS, [])

    mock_client.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_point_carries_metadata() -> None:
    mock_client = _async_qdrant_mock()
    store = VectorStore(client=mock_client)
    item = _embedded("my_func", ChunkKind.function)

    await store.upsert(COLLECTION_FUNCTIONS, [item])

    points = mock_client.upsert.call_args.kwargs["points"]
    payload = points[0].payload
    assert payload["name"] == "my_func"
    assert payload["kind"] == "function"
    assert payload["language"] == "python"
    assert "source_path" in payload


@pytest.mark.asyncio
async def test_upsert_vector_matches_embedded_chunk() -> None:
    mock_client = _async_qdrant_mock()
    store = VectorStore(client=mock_client)
    vec = np.array([0.1] * VECTOR_SIZE, dtype="float32")
    chunk = Chunk(
        source_path=Path("a.py"),
        kind=ChunkKind.function,
        name="f",
        text="def f(): pass",
        language="python",
        byte_span=(0, 10),
    )
    item = EmbeddedChunk(chunk=chunk, vector=vec)

    await store.upsert(COLLECTION_FUNCTIONS, [item])

    points = mock_client.upsert.call_args.kwargs["points"]
    assert list(points[0].vector) == pytest.approx(list(vec), abs=1e-6)


# ---------------------------------------------------------------------------
# VectorStore.search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_list_of_dicts() -> None:
    mock_client = _async_qdrant_mock()
    hit = MagicMock()
    hit.id = "abc"
    hit.score = 0.95
    hit.payload = {"name": "my_func", "kind": "function", "source_path": "src/foo.py"}
    mock_client.query_points.return_value = MagicMock(points=[hit])
    store = VectorStore(client=mock_client)

    query = np.ones(VECTOR_SIZE, dtype="float32")
    results = await store.search(COLLECTION_FUNCTIONS, query, top_k=5)

    assert len(results) == 1
    assert results[0]["score"] == pytest.approx(0.95)
    assert results[0]["payload"]["name"] == "my_func"


@pytest.mark.asyncio
async def test_search_passes_top_k_to_qdrant() -> None:
    mock_client = _async_qdrant_mock()
    store = VectorStore(client=mock_client)
    query = np.ones(VECTOR_SIZE, dtype="float32")

    await store.search(COLLECTION_DOCS, query, top_k=10)

    call_kwargs = mock_client.query_points.call_args.kwargs
    assert call_kwargs["limit"] == 10
    assert call_kwargs["collection_name"] == COLLECTION_DOCS
    assert call_kwargs["query"] == pytest.approx(query.tolist())


@pytest.mark.asyncio
async def test_search_with_filter_passes_filter_to_qdrant() -> None:
    mock_client = _async_qdrant_mock()
    store = VectorStore(client=mock_client)
    query = np.ones(VECTOR_SIZE, dtype="float32")
    flt = {"must": [{"key": "language", "match": {"value": "python"}}]}

    await store.search(COLLECTION_FUNCTIONS, query, top_k=5, filter=flt)

    call_kwargs = mock_client.query_points.call_args.kwargs
    assert call_kwargs["query_filter"] is not None


@pytest.mark.asyncio
async def test_search_uses_current_qdrant_query_points_api() -> None:
    mock_client = _async_qdrant_mock()
    del mock_client.search
    hit = MagicMock()
    hit.id = "abc"
    hit.score = 0.95
    hit.payload = {"name": "my_func"}
    mock_client.query_points.return_value = MagicMock(points=[hit])
    store = VectorStore(client=mock_client)

    results = await store.search(COLLECTION_FUNCTIONS, np.ones(VECTOR_SIZE, dtype="float32"))

    mock_client.query_points.assert_awaited_once()
    assert results == [{"id": "abc", "score": 0.95, "payload": {"name": "my_func"}}]


# ---------------------------------------------------------------------------
# VectorStore collection preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_collections_returns_collection_names() -> None:
    mock_client = _async_qdrant_mock()
    mock_client.get_collections.return_value = MagicMock(
        collections=[_collection(COLLECTION_FUNCTIONS), _collection(COLLECTION_DOCS)]
    )
    store = VectorStore(client=mock_client)

    names = await store.list_collections()

    assert names == {COLLECTION_FUNCTIONS, COLLECTION_DOCS}


@pytest.mark.asyncio
async def test_has_any_collection_detects_expected_collection() -> None:
    mock_client = _async_qdrant_mock()
    mock_client.get_collections.return_value = MagicMock(
        collections=[_collection(COLLECTION_FUNCTIONS)]
    )
    store = VectorStore(client=mock_client)

    assert await store.has_any_collection((COLLECTION_FUNCTIONS, COLLECTION_RULES)) is True


@pytest.mark.asyncio
async def test_has_any_collection_returns_false_when_index_is_missing() -> None:
    mock_client = _async_qdrant_mock()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    store = VectorStore(client=mock_client)

    assert await store.has_any_collection((COLLECTION_FUNCTIONS, COLLECTION_RULES)) is False


# ---------------------------------------------------------------------------
# VectorStore lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_delegates_to_client() -> None:
    mock_client = _async_qdrant_mock()
    store = VectorStore(client=mock_client)

    await store.close()

    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_without_client_is_noop() -> None:
    store = VectorStore()

    await store.close()


@pytest.mark.asyncio
async def test_constants_are_strings() -> None:
    assert isinstance(COLLECTION_FUNCTIONS, str)
    assert isinstance(COLLECTION_DOCS, str)
    assert isinstance(COLLECTION_RULES, str)
    assert isinstance(VECTOR_SIZE, int)
