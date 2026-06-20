"""Tests for ``rag.retriever``.

Both VectorStore and EmbeddingEngine are mocked so no live services are needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from rag.retriever import AgentDimension, ContextRetriever, RetrievalResult

# Suppress grpc/protobuf DeprecationWarnings on Python 3.12.
pytestmark = pytest.mark.filterwarnings("ignore:.*uses PyType_Spec.*:DeprecationWarning")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pr_payload(
    title: str = "Add login",
    filenames: list[str] | None = None,
) -> MagicMock:
    """Minimal PullRequestPayload mock."""
    if filenames is None:
        filenames = ["src/auth/login.py", "tests/test_login.py"]

    pr = MagicMock()
    pr.pull_request.title = title
    pr.pull_request.body = "Add login endpoint."

    files = []
    for fname in filenames:
        f = MagicMock()
        f.path = fname
        f.hunks = [MagicMock(context="def login(): pass")]
        files.append(f)
    pr.files = files
    return pr


def _mock_engine(dim: int = 384) -> MagicMock:
    engine = MagicMock()

    async def aencode_fn(chunks: list) -> list:
        from rag.embeddings import EmbeddedChunk

        return [EmbeddedChunk(chunk=c, vector=np.ones(dim, dtype="float32")) for c in chunks]

    engine.aencode = AsyncMock(side_effect=aencode_fn)
    return engine


def _mock_store(hits: list[dict] | None = None) -> MagicMock:
    store = MagicMock()
    store.search = AsyncMock(return_value=hits or [])
    return store


# ---------------------------------------------------------------------------
# RetrievalResult
# ---------------------------------------------------------------------------


def test_retrieval_result_has_all_dimensions() -> None:
    result = RetrievalResult(
        security=[],
        architecture=[],
        performance=[],
        tests=[],
    )
    assert result.security == []
    assert result.architecture == []
    assert result.performance == []
    assert result.tests == []


def test_retrieval_result_is_iterable_as_dict() -> None:
    hit = {"score": 0.9, "payload": {"name": "login"}}
    result = RetrievalResult(
        security=[hit],
        architecture=[],
        performance=[],
        tests=[],
    )
    data = result.as_dict()
    assert "security" in data
    assert data["security"][0]["score"] == 0.9


# ---------------------------------------------------------------------------
# AgentDimension
# ---------------------------------------------------------------------------


def test_all_four_dimensions_exist() -> None:
    dims = list(AgentDimension)
    names = {d.value for d in dims}
    assert "security" in names
    assert "architecture" in names
    assert "performance" in names
    assert "tests" in names


# ---------------------------------------------------------------------------
# ContextRetriever.retrieve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_returns_retrieval_result() -> None:
    retriever = ContextRetriever(
        engine=_mock_engine(),
        store=_mock_store(),
    )
    pr = _make_pr_payload()
    result = await retriever.retrieve(pr)

    assert isinstance(result, RetrievalResult)


@pytest.mark.asyncio
async def test_retrieve_calls_store_search_for_each_dimension() -> None:
    store = _mock_store()
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload()

    await retriever.retrieve(pr)

    # At least 4 searches (one per dimension, each may query multiple collections).
    assert store.search.await_count >= 4


@pytest.mark.asyncio
async def test_retrieve_returns_hits_from_store() -> None:
    hit = {"id": "1", "score": 0.95, "payload": {"name": "authenticate"}}
    store = _mock_store(hits=[hit])
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload()

    result = await retriever.retrieve(pr)

    assert any(
        len(v) > 0 for v in [result.security, result.architecture, result.performance, result.tests]
    )


@pytest.mark.asyncio
async def test_retrieve_falls_back_gracefully_when_store_raises() -> None:
    store = MagicMock()
    store.search = AsyncMock(side_effect=Exception("qdrant down"))
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload()

    result = await retriever.retrieve(pr)

    assert isinstance(result, RetrievalResult)
    assert result.security == []
    assert result.architecture == []
    assert result.performance == []
    assert result.tests == []


@pytest.mark.asyncio
async def test_retrieve_encodes_query_from_pr_context() -> None:
    engine = _mock_engine()
    store = _mock_store()
    retriever = ContextRetriever(engine=engine, store=store)
    pr = _make_pr_payload(filenames=["src/auth/login.py"])

    await retriever.retrieve(pr)

    engine.aencode.assert_awaited_once()
    chunks_passed = engine.aencode.call_args[0][0]
    assert len(chunks_passed) == 1
    assert "login" in chunks_passed[0].text or "src/auth/login.py" in chunks_passed[0].text


@pytest.mark.asyncio
async def test_retrieve_deduplicates_results_by_payload_name() -> None:
    hit_a = {"id": "1", "score": 0.95, "payload": {"name": "auth"}}
    hit_b = {"id": "2", "score": 0.85, "payload": {"name": "auth"}}
    store = _mock_store(hits=[hit_a, hit_b])
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload()

    result = await retriever.retrieve(pr)

    # After dedup, the same name should appear at most once per dimension.
    for hits in [result.security, result.architecture, result.performance, result.tests]:
        names = [h["payload"]["name"] for h in hits if h.get("payload", {}).get("name")]
        assert len(names) == len(set(names))
