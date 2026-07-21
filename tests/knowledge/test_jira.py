from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from configs.schema import KnowledgeConnectorsSettings
from knowledge.connectors import KnowledgeConnectorRequest
from knowledge.jira import (
    MANAGED_COMMENT_MARKER,
    JiraClientError,
    JiraConnector,
    extract_jira_issue_keys,
)


@dataclass
class FakeJiraClient:
    issues: dict[str, Mapping[str, object]] = field(default_factory=dict)
    comments: dict[str, list[Mapping[str, object]]] = field(default_factory=dict)
    created: list[tuple[str, str]] = field(default_factory=list)
    updated: list[tuple[str, str, str]] = field(default_factory=list)
    fail_fetch: bool = False
    fail_write: bool = False

    def fetch_issue(self, key: str) -> Mapping[str, object]:
        if self.fail_fetch:
            raise JiraClientError("jira unavailable token: secret-value")
        issue = self.issues.get(key)
        if issue is None:
            raise JiraClientError("not found")
        return issue

    def list_comments(self, key: str) -> Sequence[Mapping[str, object]]:
        if self.fail_write:
            raise JiraClientError("comment lookup failed")
        return self.comments.get(key, [])

    def create_comment(self, key: str, body: str) -> str:
        if self.fail_write:
            raise JiraClientError("comment create failed")
        self.created.append((key, body))
        return "created-1"

    def update_comment(self, key: str, comment_id: str, body: str) -> str:
        if self.fail_write:
            raise JiraClientError("comment update failed")
        self.updated.append((key, comment_id, body))
        return comment_id


def _connector(
    body: dict[str, object],
    *,
    token: str | None = "jira-token",
    client: FakeJiraClient | None = None,
) -> JiraConnector:
    settings = KnowledgeConnectorsSettings.model_validate(body)
    return JiraConnector(settings.jira, token=token, client=client)


def _issue(
    key: str,
    *,
    summary: str = "Export endpoint should require admin auth",
    status: str = "In Progress",
    labels: list[str] | None = None,
    description: object = "Review export authorization before launch.",
) -> Mapping[str, object]:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "labels": labels or ["security", "api"],
            "description": description,
        },
    }


def test_extract_jira_issue_keys_deduplicates_and_normalizes() -> None:
    keys = extract_jira_issue_keys("fix app-12 and APP-12", "Related to SEC2-90.")

    assert keys == ("APP-12", "SEC2-90")


def test_jira_disabled_is_fail_open() -> None:
    connector = _connector({"jira": {"enabled": False}})

    health = connector.is_available()
    items = connector.retrieve(KnowledgeConnectorRequest(repo="owner/repo", pr_number=1))

    assert health.available is False
    assert health.reason == "disabled"
    assert items == []


def test_jira_requires_base_url_and_token_without_contacting_jira() -> None:
    missing_url = _connector({"jira": {"enabled": True}}, client=FakeJiraClient())
    missing_token = _connector(
        {"jira": {"enabled": True, "base_url": "https://jira.example.test"}},
        token="",
        client=FakeJiraClient(),
    )

    assert missing_url.is_available().reason == "no Jira base_url configured"
    assert missing_token.is_available().reason == "JIRA_API_TOKEN is not set"


def test_jira_retrieves_source_labeled_issue_context() -> None:
    client = FakeJiraClient(
        issues={
            "SEC-42": _issue(
                "SEC-42",
                description={
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Use admin auth before export. token: secret-token",
                                }
                            ],
                        }
                    ],
                },
            )
        }
    )
    connector = _connector(
        {"jira": {"enabled": True, "base_url": "https://jira.example.test"}},
        client=client,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            query="Implements linked issue SEC-42",
            metadata={"pr_title": "SEC-42 export hardening"},
        )
    )

    assert len(items) == 1
    assert items[0].source_id == "jira:SEC-42"
    assert items[0].source_kind.value == "issue_tracker"
    assert items[0].title == "SEC-42: Export endpoint should require admin auth"
    assert items[0].url == "https://jira.example.test/browse/SEC-42"
    assert "Status: In Progress" in items[0].body
    assert "Labels: security, api" in items[0].body
    assert "secret-token" not in items[0].body
    assert "[REDACTED]" in items[0].body
    assert items[0].metadata["provider"] == "jira"
    assert items[0].metadata["trust"] == "untrusted"


def test_jira_retrieve_respects_request_and_configured_limits() -> None:
    client = FakeJiraClient(
        issues={
            "APP-1": _issue("APP-1"),
            "APP-2": _issue("APP-2"),
            "APP-3": _issue("APP-3"),
        }
    )
    connector = _connector(
        {
            "jira": {
                "enabled": True,
                "base_url": "https://jira.example.test",
                "max_items": 2,
            }
        },
        client=client,
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            query="APP-1 APP-2 APP-3",
            max_items=3,
        )
    )

    assert [item.source_id for item in items] == ["jira:APP-1", "jira:APP-2"]


def test_jira_retrieve_fails_open_when_client_fails() -> None:
    connector = _connector(
        {"jira": {"enabled": True, "base_url": "https://jira.example.test"}},
        client=FakeJiraClient(fail_fetch=True),
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="SEC-42")
    )

    assert items == []


def test_jira_managed_comment_write_is_opt_in() -> None:
    connector = _connector(
        {"jira": {"enabled": True, "base_url": "https://jira.example.test"}},
        client=FakeJiraClient(),
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "skipped"
    assert result.reason == "write mode disabled"


def test_jira_managed_comment_can_be_disabled_even_when_write_enabled() -> None:
    connector = _connector(
        {
            "jira": {
                "enabled": True,
                "base_url": "https://jira.example.test",
                "write_enabled": True,
                "managed_comments": False,
            }
        },
        client=FakeJiraClient(),
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "skipped"
    assert result.reason == "managed comments disabled"


def test_jira_managed_comment_creates_comment_with_marker() -> None:
    client = FakeJiraClient()
    connector = _connector(
        {
            "jira": {
                "enabled": True,
                "base_url": "https://jira.example.test",
                "write_enabled": True,
            }
        },
        client=client,
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "created"
    assert result.comment_id == "created-1"
    assert client.created == [("SEC-42", f"{MANAGED_COMMENT_MARKER}\nOpenRabbit summary")]


def test_jira_managed_comment_updates_existing_marker_comment() -> None:
    client = FakeJiraClient(
        comments={"SEC-42": [{"id": "comment-7", "body": f"{MANAGED_COMMENT_MARKER}\nOld"}]}
    )
    connector = _connector(
        {
            "jira": {
                "enabled": True,
                "base_url": "https://jira.example.test",
                "write_enabled": True,
            }
        },
        client=client,
    )

    result = connector.publish_managed_comment("SEC-42", "New summary")

    assert result.action == "updated"
    assert result.comment_id == "comment-7"
    assert client.updated == [("SEC-42", "comment-7", f"{MANAGED_COMMENT_MARKER}\nNew summary")]
    assert client.created == []


def test_jira_managed_comment_fails_open_when_write_fails() -> None:
    connector = _connector(
        {
            "jira": {
                "enabled": True,
                "base_url": "https://jira.example.test",
                "write_enabled": True,
            }
        },
        client=FakeJiraClient(fail_write=True),
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "failed"
    assert result.reason == "comment lookup failed"
