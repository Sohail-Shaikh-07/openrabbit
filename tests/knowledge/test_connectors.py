from __future__ import annotations

import pytest

from knowledge.connectors import (
    KnowledgeConnector,
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)


class FakeConnector:
    name = "fake"
    source_kind = KnowledgeSourceKind.MCP

    def is_available(self) -> KnowledgeConnectorHealth:
        return KnowledgeConnectorHealth(
            name=self.name, source_kind=self.source_kind, available=True
        )

    def retrieve(self, request: KnowledgeConnectorRequest) -> list[KnowledgeItem]:
        return [
            KnowledgeItem(
                source_id="fake:item:1",
                source_kind=self.source_kind,
                title="Design note",
                body=f"Review {request.repo} PR {request.pr_number}",
            )
        ]


def test_fake_connector_satisfies_protocol() -> None:
    connector: KnowledgeConnector = FakeConnector()
    request = KnowledgeConnectorRequest(repo="owner/repo", pr_number=42)

    assert connector.is_available().available is True
    assert connector.retrieve(request)[0].body == "Review owner/repo PR 42"


def test_request_validates_pr_number_and_max_items() -> None:
    assert KnowledgeConnectorRequest(repo="owner/repo", pr_number=1, max_items=50).max_items == 50

    for kwargs in (
        {"repo": "", "pr_number": 1},
        {"repo": "owner/repo", "pr_number": 0},
        {"repo": "owner/repo", "pr_number": 1, "max_items": 0},
        {"repo": "owner/repo", "pr_number": 1, "max_items": 51},
    ):
        with pytest.raises(ValueError):
            KnowledgeConnectorRequest(**kwargs)


def test_sanitize_knowledge_text_redacts_common_secrets_and_bounds_text() -> None:
    text = "token=super-secret-value and sk-abcdefghijklmnopqrstuvwxyz " + ("x" * 2000)

    sanitized = sanitize_knowledge_text(text, max_chars=80)

    assert "super-secret-value" not in sanitized
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in sanitized
    assert "token=[REDACTED]" in sanitized
    assert len(sanitized) <= 80
    assert sanitized.endswith("...")


def test_normalize_knowledge_items_filters_empty_and_bounds_items() -> None:
    items = [
        KnowledgeItem(
            source_id="one",
            source_kind=KnowledgeSourceKind.WEB_SEARCH,
            title="First",
            body="token=super-secret-value",
            score=0.2,
        ),
        KnowledgeItem(
            source_id="empty",
            source_kind=KnowledgeSourceKind.WEB_SEARCH,
            title="",
            body="",
        ),
        KnowledgeItem(
            source_id="two",
            source_kind=KnowledgeSourceKind.WEB_SEARCH,
            title="Second",
            body="Body",
            score=0.9,
        ),
    ]

    normalized = normalize_knowledge_items(items, max_items=2, max_body_chars=20)

    assert [item.source_id for item in normalized] == ["two", "one"]
    assert "super-secret-value" not in normalized[1].body
    assert normalized[1].body == "token=[REDACTED]"
