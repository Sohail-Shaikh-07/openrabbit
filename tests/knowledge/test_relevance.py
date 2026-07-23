from __future__ import annotations

from knowledge.connectors import KnowledgeConnectorRequest, KnowledgeItem, KnowledgeSourceKind
from knowledge.relevance import score_connector_items


def _request() -> KnowledgeConnectorRequest:
    return KnowledgeConnectorRequest(
        repo="owner/openrabbit",
        pr_number=42,
        changed_paths=("src/auth/export.py",),
        changed_symbols=("export_admin_report",),
        query="Fixes SEC-42. Add admin authorization for export reports.",
        metadata={"linked_issues": "SEC-42 Admin-only exports"},
    )


def test_score_connector_items_prefers_issue_path_symbol_and_repo_matches() -> None:
    items = [
        KnowledgeItem(
            source_id="generic-doc",
            source_kind=KnowledgeSourceKind.DOCUMENT,
            title="Generic docs",
            body="General product notes.",
            score=0.7,
        ),
        KnowledgeItem(
            source_id="SEC-42",
            source_kind=KnowledgeSourceKind.ISSUE_TRACKER,
            title="Admin-only exports",
            body="export_admin_report must require admin authorization.",
            url="https://jira.example/browse/SEC-42",
            repo="owner/openrabbit",
            path="src/auth/export.py",
            score=0.3,
            metadata={"provider": "jira"},
        ),
    ]

    result = score_connector_items(_request(), items, max_items=2)

    assert [item.source_id for item in result.items] == ["SEC-42", "generic-doc"]
    assert result.items[0].score == 1.0
    assert result.items[0].metadata["relevance_reasons"] == (
        "provider_score,issue_key,changed_path,changed_symbol,repo,text_overlap,source_kind"
    )
    assert result.items[0].metadata["provider_score"] == "0.3000"
    assert result.dropped_items == 0
    assert result.dropped_reasons == {}


def test_score_connector_items_drops_weak_matches_and_reports_limit_drops() -> None:
    items = [
        KnowledgeItem(
            source_id="weak",
            source_kind=KnowledgeSourceKind.DOCUMENT,
            title="Unrelated",
            body="A far away topic.",
        ),
        KnowledgeItem(
            source_id="SEC-42",
            source_kind=KnowledgeSourceKind.ISSUE_TRACKER,
            title="Admin-only exports",
            body="SEC-42 requires export authorization.",
            score=0.4,
        ),
        KnowledgeItem(
            source_id="src/auth/export.py",
            source_kind=KnowledgeSourceKind.MULTI_REPO,
            title="Shared export helper",
            body="export_admin_report helper pattern.",
            path="src/auth/export.py",
            score=0.4,
        ),
    ]

    result = score_connector_items(_request(), items, max_items=1)

    assert len(result.items) == 1
    assert result.dropped_items == 2
    assert result.dropped_reasons == {
        "weak_connector_relevance": 1,
        "connector_item_limit": 1,
    }
    assert result.scores["count"] == 1
