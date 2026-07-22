from __future__ import annotations

from knowledge.diagnostics import build_context_precision_diagnostics
from rag.retriever import RetrievalResult


def test_build_context_precision_diagnostics_summarizes_rag_and_connector_context() -> None:
    retrieval = RetrievalResult(
        security=[
            {
                "id": "rules",
                "score": 0.8,
                "payload": {
                    "name": "rules",
                    "source_path": "AGENTS.md",
                    "text": "Follow repository rules.",
                    "retrieval_reason": "repository_guideline",
                },
            },
            {
                "id": "connector:jira:PROJ-1",
                "score": 0.9,
                "payload": {
                    "name": "PROJ-1",
                    "source_path": "https://jira.example/browse/PROJ-1",
                    "kind": "connector_context",
                    "connector": "jira",
                    "text": "Connector context.",
                    "retrieval_reason": "connector:issue_tracker",
                },
            },
        ],
        diagnostics={
            "retriever": {
                "candidate_items": 4,
                "selected_items": 1,
                "dropped_items": 3,
                "dropped_reasons": {"top_k_limit": 3},
            }
        },
    )

    diagnostics = build_context_precision_diagnostics(
        retrieval,
        connector_context={
            "enabled": 1,
            "available": 1,
            "candidate_items": 3,
            "items": 1,
            "dropped_items": 2,
            "dropped_reasons": {"connector_item_limit": 2},
            "sources": {"jira": 1},
        },
        command="review",
    )

    assert diagnostics["command"] == "review"
    assert diagnostics["candidate_items"] == 7
    assert diagnostics["selected_items"] == 2
    assert diagnostics["dropped_items"] == 5
    assert diagnostics["selected_sources"] == {"AGENTS.md": 1, "jira": 1}
    assert diagnostics["selected_reasons"] == {
        "connector:issue_tracker": 1,
        "repository_guideline": 1,
    }
    assert diagnostics["scores"] == {"count": 2, "min": 0.8, "max": 0.9, "avg": 0.85}
    assert diagnostics["rag"]["dropped_reasons"] == {"top_k_limit": 3}
    assert diagnostics["connectors"]["dropped_reasons"] == {"connector_item_limit": 2}
    assert diagnostics["prompt_packing"]["context_items"] == 2
    assert diagnostics["prompt_packing"]["estimated_tokens"] > 0
