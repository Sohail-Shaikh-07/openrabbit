"""Tests for ``rag.retriever``.

Both VectorStore and EmbeddingEngine are mocked so no live services are needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from rag.retriever import AgentDimension, ContextRetriever, RetrievalResult
from rag.vector_store import (
    COLLECTION_DOCS,
    COLLECTION_FUNCTIONS,
    COLLECTION_REVIEWS,
    COLLECTION_RULES,
)

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
    store.has_any_collection = AsyncMock(return_value=True)
    return store


def _payload_hit(name: str, path: str, score: float = 0.9) -> dict:
    return {"id": f"{path}:{name}", "score": score, "payload": {"name": name, "source_path": path}}


def _guideline_hit() -> dict:
    return {
        "id": "services/api/AGENTS.md:api-rules",
        "score": 0.88,
        "payload": {
            "name": "api-rules",
            "source_path": "services/api/AGENTS.md",
            "kind": "section",
            "rule_source": "repository_guideline",
            "scope_path": "services/api",
            "guideline_path": "services/api/AGENTS.md",
        },
    }


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
    store.has_any_collection = AsyncMock(return_value=True)
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
async def test_retrieve_skips_embedding_when_rag_collections_are_missing() -> None:
    engine = _mock_engine()
    store = _mock_store()
    store.has_any_collection.return_value = False
    retriever = ContextRetriever(engine=engine, store=store)
    pr = _make_pr_payload()

    result = await retriever.retrieve(pr)

    store.has_any_collection.assert_awaited_once()
    engine.aencode.assert_not_awaited()
    store.search.assert_not_awaited()
    assert result.security == []
    assert result.diagnostics["retriever"]["available"] is False
    assert result.diagnostics["retriever"]["dropped_reasons"] == {"rag_index_unavailable": 1}


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
async def test_retrieve_query_includes_title_body_paths_and_hunk_lines() -> None:
    engine = _mock_engine()
    store = _mock_store()
    retriever = ContextRetriever(engine=engine, store=store)
    pr = _make_pr_payload(title="Add task export", filenames=["src/tasks/export.py"])
    pr.pull_request.body = "Adds CSV export for filtered tasks."
    pr.files[0].hunks = [
        MagicMock(
            lines=[
                MagicMock(text="def export_tasks():"),
                MagicMock(text="return csv_data"),
            ]
        )
    ]

    await retriever.retrieve(pr)

    query_text = engine.aencode.call_args[0][0][0].text
    assert "Add task export" in query_text
    assert "CSV export" in query_text
    assert "src/tasks/export.py" in query_text
    assert "def export_tasks():" in query_text
    assert "return csv_data" in query_text


@pytest.mark.asyncio
async def test_retrieve_query_includes_changed_symbol_hints() -> None:
    engine = _mock_engine()
    store = _mock_store()
    retriever = ContextRetriever(engine=engine, store=store)
    pr = _make_pr_payload(title="Add task export", filenames=["src/tasks/export.py"])
    pr.files[0].hunks = [
        MagicMock(
            lines=[
                MagicMock(text="class TaskExporter:"),
                MagicMock(text="def export_tasks(self):"),
                MagicMock(text="async def stream_tasks(self):"),
            ]
        )
    ]

    await retriever.retrieve(pr)

    query_text = engine.aencode.call_args[0][0][0].text
    assert "changed symbols:" in query_text
    assert "TaskExporter" in query_text
    assert "export_tasks" in query_text
    assert "stream_tasks" in query_text


@pytest.mark.asyncio
async def test_retrieve_query_includes_related_test_and_nearby_directory_hints() -> None:
    engine = _mock_engine()
    store = _mock_store()
    retriever = ContextRetriever(engine=engine, store=store)
    pr = _make_pr_payload(title="Add task export", filenames=["src/tasks/export.py"])

    await retriever.retrieve(pr)

    query_text = engine.aencode.call_args[0][0][0].text
    assert "nearby directories:" in query_text
    assert "src/tasks" in query_text
    assert "related tests:" in query_text
    assert "tests/tasks/test_export.py" in query_text


@pytest.mark.asyncio
async def test_retrieve_prioritizes_changed_file_context_with_path_filters() -> None:
    store = _mock_store()

    async def search_side_effect(
        collection: str,
        _query_vec: object,
        *,
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[dict]:
        _ = top_k
        if collection == COLLECTION_FUNCTIONS and filter:
            return [_payload_hit("changed-func", "src/auth/login.py")]
        if collection == COLLECTION_DOCS:
            return [_payload_hit("architecture-doc", "docs/architecture.md")]
        if collection == COLLECTION_REVIEWS:
            return [_payload_hit("review-example", ".openrabbit/review_examples.md")]
        return [_payload_hit("security-rule", ".openrabbit/security_rules.md")]

    store.search.side_effect = search_side_effect
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload(filenames=["src/auth/login.py"])

    result = await retriever.retrieve(pr)

    filtered_calls = [
        call for call in store.search.await_args_list if call.kwargs.get("filter", {}).get("should")
    ]
    assert filtered_calls
    assert any(
        condition["key"] == "source_path" and condition["match"]["value"] == "src/auth/login.py"
        for call in filtered_calls
        for condition in call.kwargs["filter"]["should"]
    )
    assert result.performance[0]["payload"]["source_path"] == "src/auth/login.py"


@pytest.mark.asyncio
async def test_retrieve_path_filter_includes_all_changed_files() -> None:
    store = _mock_store()
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload(filenames=["src/auth/login.py", "src/auth/session.py"])

    await retriever.retrieve(pr)

    filtered_calls = [
        call for call in store.search.await_args_list if call.kwargs.get("filter", {}).get("should")
    ]
    assert filtered_calls
    values = {
        condition["match"]["value"]
        for call in filtered_calls
        for condition in call.kwargs["filter"]["should"]
        if condition["key"] == "source_path"
    }
    assert {"src/auth/login.py", "src/auth/session.py"}.issubset(values)
    assert "tests/auth/test_login.py" in values
    assert "tests/auth/test_session.py" in values


@pytest.mark.asyncio
async def test_retrieve_queries_function_context_by_changed_symbols() -> None:
    store = _mock_store()
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload(title="Add task export", filenames=["src/tasks/export.py"])
    pr.files[0].hunks = [
        MagicMock(
            lines=[
                MagicMock(text="def export_tasks():"),
                MagicMock(text="return csv_data"),
            ]
        )
    ]

    await retriever.retrieve(pr)

    symbol_calls = [
        call
        for call in store.search.await_args_list
        if call.args[0] == COLLECTION_FUNCTIONS
        and any(
            condition["key"] == "name" and condition["match"]["value"] == "export_tasks"
            for condition in call.kwargs.get("filter", {}).get("should", [])
        )
    ]
    assert symbol_calls


@pytest.mark.asyncio
async def test_retrieve_queries_related_test_paths_and_labels_results() -> None:
    store = _mock_store()

    async def search_side_effect(
        collection: str,
        _query_vec: object,
        *,
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[dict]:
        _ = top_k
        if collection != COLLECTION_FUNCTIONS:
            return []
        conditions = (filter or {}).get("should", [])
        if any(
            condition["key"] == "source_path"
            and condition["match"]["value"] == "tests/tasks/test_export.py"
            for condition in conditions
        ):
            return [_payload_hit("test_export", "tests/tasks/test_export.py", score=0.2)]
        return [_payload_hit("semantic-helper", "src/other.py", score=0.99)]

    store.search.side_effect = search_side_effect
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload(title="Add task export", filenames=["src/tasks/export.py"])

    result = await retriever.retrieve(pr)

    assert result.tests[0]["payload"]["source_path"] == "tests/tasks/test_export.py"
    assert result.tests[0]["payload"]["retrieval_reason"] == "related_test"


@pytest.mark.asyncio
async def test_retrieve_queries_scoped_guidelines_and_architecture_docs() -> None:
    store = _mock_store()
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload(filenames=["services/api/export.py"])

    await retriever.retrieve(pr)

    rule_calls = [
        call
        for call in store.search.await_args_list
        if call.args[0] == COLLECTION_RULES and call.kwargs.get("filter")
    ]
    assert rule_calls
    rule_scopes = {
        condition["match"]["value"]
        for call in rule_calls
        for condition in call.kwargs["filter"]["should"]
        if condition["key"] == "scope_path"
    }
    assert {".", "services", "services/api"}.issubset(rule_scopes)

    doc_calls = [
        call
        for call in store.search.await_args_list
        if call.args[0] == COLLECTION_DOCS and call.kwargs.get("filter")
    ]
    assert doc_calls
    doc_paths = {
        condition["match"]["value"]
        for call in doc_calls
        for condition in call.kwargs["filter"]["should"]
        if condition["key"] == "source_path"
    }
    assert "docs/architecture.md" in doc_paths
    assert "docs/services/api/architecture.md" in doc_paths


@pytest.mark.asyncio
async def test_retrieve_packs_changed_file_context_before_broader_matches() -> None:
    store = _mock_store()

    async def search_side_effect(
        collection: str,
        _query_vec: object,
        *,
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[dict]:
        _ = top_k
        if collection != COLLECTION_FUNCTIONS:
            return []
        conditions = (filter or {}).get("should", [])
        if any(condition["key"] == "source_path" for condition in conditions):
            return [_payload_hit("shared", "src/auth/login.py", score=0.1)]
        if any(condition["key"] == "name" for condition in conditions):
            return [_payload_hit("export_tasks", "src/tasks/helpers.py", score=0.8)]
        return [
            _payload_hit("shared", "src/auth/unrelated.py", score=0.99),
            _payload_hit("semantic-helper", "src/auth/session.py", score=0.7),
        ]

    store.search.side_effect = search_side_effect
    retriever = ContextRetriever(engine=_mock_engine(), store=store)
    pr = _make_pr_payload(filenames=["src/auth/login.py"])
    pr.files[0].hunks = [MagicMock(lines=[MagicMock(text="def export_tasks():")])]

    result = await retriever.retrieve(pr)

    assert result.performance[0]["payload"]["source_path"] == "src/auth/login.py"
    assert result.performance[0]["payload"]["retrieval_reason"] == "changed_file"
    assert all(
        hit["payload"]["source_path"] != "src/auth/unrelated.py" for hit in result.performance
    )


@pytest.mark.asyncio
async def test_retrieve_labels_global_guidelines_as_repository_guidelines() -> None:
    store = _mock_store()

    async def search_side_effect(
        collection: str,
        _query_vec: object,
        *,
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[dict]:
        _ = top_k, filter
        if collection != COLLECTION_RULES:
            return []
        return [
            {
                "id": "AGENTS.md:rules",
                "score": 0.92,
                "payload": {
                    "name": "rules",
                    "source_path": "AGENTS.md",
                    "kind": "section",
                    "rule_source": "repository_guideline",
                    "guideline_path": "AGENTS.md",
                },
            }
        ]

    store.search.side_effect = search_side_effect
    retriever = ContextRetriever(engine=_mock_engine(), store=store)

    result = await retriever.retrieve(_make_pr_payload())

    assert result.security[0]["payload"]["retrieval_reason"] == "repository_guideline"


@pytest.mark.asyncio
async def test_retrieve_prioritizes_architecture_docs_in_architecture_dimension() -> None:
    store = _mock_store()

    async def search_side_effect(
        collection: str,
        _query_vec: object,
        *,
        top_k: int = 10,
        filter: dict | None = None,
    ) -> list[dict]:
        _ = top_k
        if collection != COLLECTION_DOCS:
            return []
        if filter:
            return [_payload_hit("architecture", "docs/architecture.md", score=0.1)]
        return [_payload_hit("general-doc", "docs/general.md", score=0.99)]

    store.search.side_effect = search_side_effect
    retriever = ContextRetriever(engine=_mock_engine(), store=store)

    result = await retriever.retrieve(_make_pr_payload(filenames=["src/auth/login.py"]))

    assert result.architecture[0]["payload"]["source_path"] == "docs/architecture.md"
    assert result.architecture[0]["payload"]["retrieval_reason"] == "architecture_doc"


def test_retrieval_result_exposes_context_provenance() -> None:
    security_hit = _payload_hit("security-rule", ".openrabbit/security_rules.md", 0.91)
    security_hit["payload"]["retrieval_reason"] = "semantic"
    result = RetrievalResult(
        security=[security_hit],
        architecture=[_payload_hit("architecture", "docs/architecture.md", 0.82)],
    )

    provenance = result.provenance()

    assert provenance[0]["dimension"] == "security"
    assert provenance[0]["source_path"] == ".openrabbit/security_rules.md"
    assert provenance[0]["name"] == "security-rule"
    assert provenance[0]["score"] == 0.91
    assert provenance[0]["retrieval_reason"] == "semantic"


@pytest.mark.asyncio
async def test_retrieve_records_candidate_and_dropped_diagnostics() -> None:
    hits = [_payload_hit(f"candidate-{index}", f"src/file_{index}.py") for index in range(12)]
    store = _mock_store(hits=hits)
    retriever = ContextRetriever(engine=_mock_engine(), store=store, top_k=3)

    result = await retriever.retrieve(_make_pr_payload())

    diagnostics = result.diagnostics["retriever"]
    assert diagnostics["available"] is True
    assert diagnostics["candidate_items"] > diagnostics["selected_items"]
    assert diagnostics["dropped_reasons"]["top_k_limit"] > 0
    assert diagnostics["dimensions"]["security"]["selected_items"] == 3


def test_retrieval_result_provenance_includes_guideline_metadata() -> None:
    result = RetrievalResult(security=[_guideline_hit()])

    provenance = result.provenance()

    assert provenance[0]["rule_source"] == "repository_guideline"
    assert provenance[0]["scope_path"] == "services/api"
    assert provenance[0]["guideline_path"] == "services/api/AGENTS.md"


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
