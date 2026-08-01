"""Tests for mapping webhook payloads into the shared review handler."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api import WebhookEventDispatcher, WebhookPayloadError, map_webhook_event
from github_ import PollEvent, PullRequest, RepositoryHandle


def _pull_request(number: int = 42) -> dict[str, object]:
    return {
        "number": number,
        "title": "Add webhook dispatch",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "feature", "sha": "a" * 40, "label": "alice:feature"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T01:00:00Z",
        "labels": [],
    }


def _comment(body: str = "/openrabbit review") -> dict[str, object]:
    return {
        "id": 100,
        "user": {"login": "alice", "id": 1},
        "body": body,
        "html_url": "https://github.com/o/r/pull/42#issuecomment-100",
        "created_at": "2026-08-01T01:00:00Z",
        "updated_at": "2026-08-01T01:00:00Z",
    }


def _payload(**values: object) -> dict[str, object]:
    return {"repository": {"full_name": "o/r"}, **values}


@pytest.mark.parametrize(
    "action,event_kind",
    [
        ("opened", "pull_request_opened"),
        ("reopened", "pull_request_opened"),
        ("ready_for_review", "pull_request_opened"),
        ("synchronize", "commit_pushed"),
    ],
)
def test_pull_request_actions_map_to_polling_events(action: str, event_kind: str) -> None:
    plan = map_webhook_event(
        "pull_request",
        _payload(action=action, pull_request=_pull_request()),
        expected_repository="O/R",
    )

    assert plan.reason == "scheduled"
    assert plan.repository == "o/r"
    assert plan.request is not None
    assert plan.request.event_kind == event_kind
    assert plan.request.pr_number == 42


def test_non_actionable_pull_request_action_is_ignored() -> None:
    plan = map_webhook_event(
        "pull_request",
        _payload(action="closed", pull_request=_pull_request()),
        expected_repository="o/r",
    )

    assert plan.request is None
    assert plan.reason == "pull_request_action_closed_ignored"


def test_ping_is_ignored_after_repository_validation() -> None:
    plan = map_webhook_event(
        "ping",
        _payload(zen="Keep it logically awesome"),
        expected_repository="o/r",
    )

    assert plan.request is None
    assert plan.reason == "ping"


def test_issue_comment_command_maps_to_canonical_comment_dispatch() -> None:
    plan = map_webhook_event(
        "issue_comment",
        _payload(
            action="created",
            issue={"number": 42, "pull_request": {"url": "https://api.github.com/pulls/42"}},
            comment=_comment("/openrabbit ask what changed?"),
        ),
        expected_repository="o/r",
    )

    assert plan.request is not None
    assert plan.request.event_kind is None
    assert plan.request.pr_number == 42
    assert plan.request.source_comment_id == 100


@pytest.mark.parametrize(
    "payload,reason",
    [
        (
            _payload(
                action="created",
                issue={"number": 42, "pull_request": {}},
                comment=_comment("Looks good"),
            ),
            "issue_comment_without_command",
        ),
        (
            _payload(action="created", issue={"number": 42}, comment=_comment()),
            "issue_comment_not_on_pull_request",
        ),
        (
            _payload(
                action="edited",
                issue={"number": 42, "pull_request": {}},
                comment=_comment(),
            ),
            "issue_comment_action_edited_ignored",
        ),
    ],
)
def test_non_actionable_issue_comments_are_ignored(
    payload: dict[str, object],
    reason: str,
) -> None:
    plan = map_webhook_event("issue_comment", payload, expected_repository="o/r")

    assert plan.request is None
    assert plan.reason == reason


@pytest.mark.parametrize(
    "event,payload,message",
    [
        ("ping", _payload(), "repository.target"),
        ("pull_request", _payload(action="opened"), "pull_request data"),
        ("pull_request", _payload(pull_request=_pull_request()), "missing action"),
        (
            "issue_comment",
            _payload(action="created", issue={"number": 0, "pull_request": {}}, comment=_comment()),
            "invalid issue number",
        ),
    ],
)
def test_malformed_or_misrouted_payloads_fail_before_dispatch(
    event: str,
    payload: dict[str, object],
    message: str,
) -> None:
    target = "different/repo" if event == "ping" else "o/r"
    with pytest.raises(WebhookPayloadError, match=message):
        map_webhook_event(event, payload, expected_repository=target)


async def test_dispatcher_uses_payload_pr_for_review_event() -> None:
    handler = AsyncMock()
    handle = AsyncMock(spec=RepositoryHandle)
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=handle,
        handler=handler,
    )
    plan = dispatcher.prepare(
        "pull_request",
        _payload(action="synchronize", pull_request=_pull_request()),
    )
    assert plan.request is not None

    await dispatcher.dispatch(plan.request)

    handle.get_pull_request.assert_not_called()
    event = handler.await_args.args[0]
    assert isinstance(event, PollEvent)
    assert event.kind == "commit_pushed"
    assert event.number == 42


async def test_dispatcher_fetches_canonical_pr_for_comment_command() -> None:
    handler = AsyncMock()
    handle = AsyncMock(spec=RepositoryHandle)
    handle.get_pull_request.return_value = PullRequest.model_validate(_pull_request())
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=handle,
        handler=handler,
    )
    plan = dispatcher.prepare(
        "issue_comment",
        _payload(
            action="created",
            issue={"number": 42, "pull_request": {}},
            comment=_comment(),
        ),
    )
    assert plan.request is not None

    await dispatcher.dispatch(plan.request)

    handle.get_pull_request.assert_awaited_once_with(42)
    event = handler.await_args.args[0]
    assert event.kind == "pull_request_updated"
    assert event.number == 42
