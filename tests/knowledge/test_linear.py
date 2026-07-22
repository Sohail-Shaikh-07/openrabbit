from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from configs.schema import KnowledgeConnectorsSettings
from knowledge.connectors import KnowledgeConnectorRequest
from knowledge.linear import (
    MANAGED_LINEAR_COMMENT_MARKER,
    LinearClientError,
    LinearConnector,
    extract_linear_issue_ids,
)


@dataclass
class FakeLinearClient:
    issues: dict[str, Mapping[str, object]] = field(default_factory=dict)
    comments: dict[str, list[Mapping[str, object]]] = field(default_factory=dict)
    created: list[tuple[str, str]] = field(default_factory=list)
    updated: list[tuple[str, str]] = field(default_factory=list)
    fail_fetch: bool = False
    fail_write: bool = False
    fail_fetch_message: str = "linear unavailable token: secret-value"
    fail_write_message: str = "comment lookup failed"

    def fetch_issue(self, identifier: str) -> Mapping[str, object]:
        if self.fail_fetch:
            raise LinearClientError(self.fail_fetch_message)
        issue = self.issues.get(identifier)
        if issue is None:
            raise LinearClientError("not found")
        return issue

    def list_comments(self, issue_id: str) -> Sequence[Mapping[str, object]]:
        if self.fail_write:
            raise LinearClientError(self.fail_write_message)
        return self.comments.get(issue_id, [])

    def create_comment(self, issue_id: str, body: str) -> str:
        if self.fail_write:
            raise LinearClientError(self.fail_write_message)
        self.created.append((issue_id, body))
        return "created-linear"

    def update_comment(self, comment_id: str, body: str) -> str:
        if self.fail_write:
            raise LinearClientError(self.fail_write_message)
        self.updated.append((comment_id, body))
        return comment_id


def _connector(
    body: dict[str, object],
    *,
    token: str | None = "linear-token",
    client: FakeLinearClient | None = None,
) -> LinearConnector:
    settings = KnowledgeConnectorsSettings.model_validate(body)
    return LinearConnector(settings.linear, token=token, client=client)


def _issue(
    identifier: str,
    *,
    issue_id: str = "issue-1",
    title: str = "Export endpoint should require admin auth",
    state: str = "In Progress",
    labels: list[str] | None = None,
    description: str = "Review export authorization before launch.",
) -> Mapping[str, object]:
    return {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "state": {"name": state},
        "labels": {"nodes": [{"name": label} for label in labels or ["security", "api"]]},
        "url": f"https://linear.app/openrabbit/issue/{identifier}",
        "description": description,
    }


def test_extract_linear_issue_ids_deduplicates_and_normalizes() -> None:
    identifiers = extract_linear_issue_ids("fix app-12 and APP-12", "Related to SEC2-90.")

    assert identifiers == ("APP-12", "SEC2-90")


def test_linear_disabled_is_fail_open() -> None:
    connector = _connector({"linear": {"enabled": False}})

    health = connector.is_available()
    items = connector.retrieve(KnowledgeConnectorRequest(repo="owner/repo", pr_number=1))

    assert health.available is False
    assert health.reason == "disabled"
    assert items == []


def test_linear_requires_token_without_contacting_linear() -> None:
    connector = _connector({"linear": {"enabled": True}}, token="", client=FakeLinearClient())

    assert connector.is_available().reason == "LINEAR_API_KEY is not set"


def test_linear_retrieves_source_labeled_issue_context() -> None:
    client = FakeLinearClient(
        issues={
            "SEC-42": _issue(
                "SEC-42",
                issue_id="issue-sec-42",
                description="Use admin auth before export. token: secret-token",
            )
        }
    )
    connector = _connector({"linear": {"enabled": True}}, client=client)

    items = connector.retrieve(
        KnowledgeConnectorRequest(
            repo="owner/repo",
            pr_number=42,
            query="Implements linked issue SEC-42",
            metadata={"pr_title": "SEC-42 export hardening"},
        )
    )

    assert len(items) == 1
    assert items[0].source_id == "linear:SEC-42"
    assert items[0].source_kind.value == "issue_tracker"
    assert items[0].title == "SEC-42: Export endpoint should require admin auth"
    assert items[0].url == "https://linear.app/openrabbit/issue/SEC-42"
    assert "State: In Progress" in items[0].body
    assert "Labels: security, api" in items[0].body
    assert "secret-token" not in items[0].body
    assert "[REDACTED]" in items[0].body
    assert items[0].metadata["provider"] == "linear"
    assert items[0].metadata["id"] == "issue-sec-42"
    assert items[0].metadata["trust"] == "untrusted"


def test_linear_retrieve_respects_request_and_configured_limits() -> None:
    client = FakeLinearClient(
        issues={
            "APP-1": _issue("APP-1", issue_id="issue-1"),
            "APP-2": _issue("APP-2", issue_id="issue-2"),
            "APP-3": _issue("APP-3", issue_id="issue-3"),
        }
    )
    connector = _connector(
        {"linear": {"enabled": True, "max_items": 2}},
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

    assert [item.source_id for item in items] == ["linear:APP-1", "linear:APP-2"]


def test_linear_retrieve_fails_open_when_client_fails() -> None:
    connector = _connector(
        {"linear": {"enabled": True}},
        client=FakeLinearClient(fail_fetch=True),
    )

    items = connector.retrieve(
        KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="SEC-42")
    )

    assert items == []


def test_linear_retrieve_fails_open_for_auth_rate_limit_and_malformed_responses() -> None:
    auth_failure = _connector(
        {"linear": {"enabled": True}},
        client=FakeLinearClient(
            fail_fetch=True,
            fail_fetch_message="401 unauthorized token: leaked-secret-value",
        ),
    )
    rate_limited = _connector(
        {"linear": {"enabled": True}},
        client=FakeLinearClient(
            fail_fetch=True,
            fail_fetch_message="429 rate limited token: leaked-secret-value",
        ),
    )
    malformed = _connector(
        {"linear": {"enabled": True}},
        client=FakeLinearClient(issues={"SEC-42": {"id": "", "identifier": "", "title": ""}}),
    )
    request = KnowledgeConnectorRequest(repo="owner/repo", pr_number=42, query="SEC-42")

    assert auth_failure.retrieve(request) == []
    assert rate_limited.retrieve(request) == []
    assert malformed.retrieve(request)[0].title == "SEC-42"


def test_linear_managed_comment_write_is_opt_in() -> None:
    connector = _connector(
        {"linear": {"enabled": True}},
        client=FakeLinearClient(),
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "skipped"
    assert result.reason == "write mode disabled"


def test_linear_managed_comment_can_be_disabled_even_when_write_enabled() -> None:
    connector = _connector(
        {
            "linear": {
                "enabled": True,
                "write_enabled": True,
                "managed_comments": False,
            }
        },
        client=FakeLinearClient(),
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "skipped"
    assert result.reason == "managed comments disabled"


def test_linear_managed_comment_creates_comment_with_marker() -> None:
    client = FakeLinearClient(issues={"SEC-42": _issue("SEC-42", issue_id="issue-sec-42")})
    connector = _connector(
        {"linear": {"enabled": True, "write_enabled": True}},
        client=client,
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "created"
    assert result.comment_id == "created-linear"
    assert client.created == [
        ("issue-sec-42", f"{MANAGED_LINEAR_COMMENT_MARKER}\nOpenRabbit summary")
    ]


def test_linear_managed_comment_redacts_and_bounds_body() -> None:
    client = FakeLinearClient(issues={"SEC-42": _issue("SEC-42", issue_id="issue-sec-42")})
    connector = _connector(
        {"linear": {"enabled": True, "write_enabled": True}},
        client=client,
    )
    raw_body = "token=super-secret-value " + ("x" * 7000)

    result = connector.publish_managed_comment("SEC-42", raw_body)

    assert result.action == "created"
    body = client.created[0][1]
    assert body.startswith(MANAGED_LINEAR_COMMENT_MARKER)
    assert "super-secret-value" not in body
    assert "token=[REDACTED]" in body
    assert len(body) <= len(MANAGED_LINEAR_COMMENT_MARKER) + 1 + 6000


def test_linear_managed_comment_updates_existing_marker_comment() -> None:
    client = FakeLinearClient(
        issues={"SEC-42": _issue("SEC-42", issue_id="issue-sec-42")},
        comments={
            "issue-sec-42": [{"id": "comment-7", "body": f"{MANAGED_LINEAR_COMMENT_MARKER}\nOld"}]
        },
    )
    connector = _connector(
        {"linear": {"enabled": True, "write_enabled": True}},
        client=client,
    )

    result = connector.publish_managed_comment("SEC-42", "New summary")

    assert result.action == "updated"
    assert result.comment_id == "comment-7"
    assert client.updated == [("comment-7", f"{MANAGED_LINEAR_COMMENT_MARKER}\nNew summary")]
    assert client.created == []


def test_linear_managed_comment_updates_one_existing_marker_without_duplicate_create() -> None:
    client = FakeLinearClient(
        issues={"SEC-42": _issue("SEC-42", issue_id="issue-sec-42")},
        comments={
            "issue-sec-42": [
                {"id": "comment-unmanaged", "body": "Human comment"},
                {"id": "comment-managed", "body": f"{MANAGED_LINEAR_COMMENT_MARKER}\nOld"},
                {"id": "comment-managed-later", "body": f"{MANAGED_LINEAR_COMMENT_MARKER}\nOlder"},
            ]
        },
    )
    connector = _connector(
        {"linear": {"enabled": True, "write_enabled": True}},
        client=client,
    )

    result = connector.publish_managed_comment("SEC-42", "New summary")

    assert result.action == "updated"
    assert result.comment_id == "comment-managed"
    assert client.updated == [("comment-managed", f"{MANAGED_LINEAR_COMMENT_MARKER}\nNew summary")]
    assert client.created == []


def test_linear_managed_comment_fails_open_when_write_fails() -> None:
    connector = _connector(
        {"linear": {"enabled": True, "write_enabled": True}},
        client=FakeLinearClient(
            issues={"SEC-42": _issue("SEC-42", issue_id="issue-sec-42")},
            fail_write=True,
            fail_write_message="401 unauthorized token: leaked-secret-value",
        ),
    )

    result = connector.publish_managed_comment("SEC-42", "OpenRabbit summary")

    assert result.action == "failed"
    assert result.reason == "401 unauthorized token: [REDACTED]"
    assert "leaked-secret-value" not in result.reason
