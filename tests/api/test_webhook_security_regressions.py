"""Release-blocking security regressions for GitHub webhook mode."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api import (
    GITHUB_WEBHOOK_PATH,
    SQLiteDeliveryStateStore,
    WebhookEventDispatcher,
    create_app,
)
from configs import RepositorySettings, Settings, WebhookSettings
from github_ import PullRequest, RepositoryHandle

_SECRET = "security-regression-secret"
_DELIVERY_ID = "security-delivery-1"


def _settings(*, max_payload_bytes: int = 1_024) -> Settings:
    return Settings(
        webhook=WebhookSettings(
            enabled=True,
            secret_env="TEST_WEBHOOK_SECRET",
            max_payload_bytes=max_payload_bytes,
        ),
        repository=RepositorySettings(target="o/r"),
    )


def _pull_request_payload(
    *,
    repository: str = "o/r",
    base_repository: str = "o/r",
    title: str = "Webhook security regression",
) -> dict[str, object]:
    return {
        "repository": {"full_name": repository},
        "action": "opened",
        "pull_request": {
            "number": 42,
            "title": title,
            "state": "open",
            "draft": False,
            "user": {"login": "alice", "id": 1},
            "head": {
                "ref": "feature",
                "sha": "a" * 40,
                "label": "contributor:feature",
                "repo": {
                    "full_name": "contributor/private-fork",
                    "private": True,
                },
            },
            "base": {
                "ref": "main",
                "sha": "b" * 40,
                "label": "o:main",
                "repo": {"full_name": base_repository, "private": True},
            },
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-01T01:00:00Z",
            "labels": [],
        },
    }


def _body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _signature(payload: bytes, secret: str = _SECRET) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _headers(
    payload: bytes,
    *,
    event: str = "pull_request",
    delivery_id: str = _DELIVERY_ID,
    secret: str = _SECRET,
) -> dict[str, str]:
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": _signature(payload, secret),
        "Content-Type": "application/json",
    }


def _client(
    tmp_path: Path,
    *,
    handler: AsyncMock | None = None,
    max_payload_bytes: int = 1_024,
) -> tuple[TestClient, SQLiteDeliveryStateStore, AsyncMock]:
    review_handler = handler or AsyncMock()
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=review_handler,
    )
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")
    client = TestClient(
        create_app(
            _settings(max_payload_bytes=max_payload_bytes),
            webhook_secret=_SECRET,
            dispatcher=dispatcher,
            delivery_store=store,
        )
    )
    return client, store, review_handler


@pytest.mark.parametrize(
    "payload,headers",
    [
        (
            _body(_pull_request_payload()),
            {"signature_secret": "wrong-secret", "event": "pull_request"},
        ),
        (b"not-json", {"signature_secret": _SECRET, "event": "pull_request"}),
        (
            _body(_pull_request_payload()),
            {"signature_secret": _SECRET, "event": "push"},
        ),
    ],
)
def test_rejected_request_cannot_mutate_state_or_dispatch(
    tmp_path: Path,
    payload: bytes,
    headers: dict[str, str],
) -> None:
    client, store, handler = _client(tmp_path)

    response = client.post(
        GITHUB_WEBHOOK_PATH,
        content=payload,
        headers=_headers(
            payload,
            event=headers["event"],
            secret=headers["signature_secret"],
        ),
    )

    assert response.status_code in {400, 401}
    assert store.list_recent() == []
    handler.assert_not_awaited()


def test_oversized_payload_cannot_mutate_state_or_dispatch(tmp_path: Path) -> None:
    client, store, handler = _client(tmp_path, max_payload_bytes=1_024)
    payload = b'{"repository":{"full_name":"o/r"},"padding":"' + b"x" * 1_024 + b'"}'

    response = client.post(
        GITHUB_WEBHOOK_PATH,
        content=payload,
        headers=_headers(payload),
    )

    assert response.status_code == 413
    assert store.list_recent() == []
    handler.assert_not_awaited()


@pytest.mark.parametrize(
    "payload,error",
    [
        (_pull_request_payload(repository="other/repo"), "repository.target"),
        (_pull_request_payload(base_repository="other/repo"), "base repository"),
    ],
)
def test_repository_boundary_failure_precedes_delivery_claim(
    tmp_path: Path,
    payload: dict[str, object],
    error: str,
) -> None:
    client, store, handler = _client(tmp_path)
    encoded = _body(payload)

    response = client.post(
        GITHUB_WEBHOOK_PATH,
        content=encoded,
        headers=_headers(encoded),
    )

    assert response.status_code == 400
    assert error in response.json()["detail"]
    assert store.list_recent() == []
    handler.assert_not_awaited()


def test_fork_head_is_dispatched_without_persisting_fork_metadata(tmp_path: Path) -> None:
    client, store, handler = _client(tmp_path)
    payload = _body(_pull_request_payload())

    response = client.post(
        GITHUB_WEBHOOK_PATH,
        content=payload,
        headers=_headers(payload),
    )

    assert response.status_code == 202
    assert response.json()["dispatched"] is True
    handler.assert_awaited_once()
    event = handler.await_args.args[0]
    assert "repo" not in event.pull_request.head.model_dump()
    persisted = store.path.read_bytes().decode(errors="ignore").lower()
    assert "contributor/private-fork" not in persisted
    assert "private" not in persisted


def test_failure_response_log_and_state_exclude_sensitive_values(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload_secret = "private-source-payload-value"
    exception_secret = "private-exception-value"
    handler = AsyncMock(side_effect=RuntimeError(exception_secret))
    client, store, _ = _client(tmp_path, handler=handler)
    payload = _body(_pull_request_payload(title=payload_secret))

    with caplog.at_level(logging.INFO, logger="api.server"):
        response = client.post(
            GITHUB_WEBHOOK_PATH,
            content=payload,
            headers=_headers(payload),
        )

    record = store.get(_DELIVERY_ID)
    assert response.status_code == 202
    assert record is not None
    assert record.status == "failed"
    assert record.last_error_type == "RuntimeError"
    public_surfaces = "\n".join((response.text, caplog.text, str(record)))
    persisted = store.path.read_bytes().decode(errors="ignore")
    for secret in (_SECRET, payload_secret, exception_secret):
        assert secret not in public_surfaces
        assert secret not in persisted


def test_issue_comment_payload_urls_do_not_control_canonical_repository_fetch(
    tmp_path: Path,
) -> None:
    handle = AsyncMock(spec=RepositoryHandle)
    handle.get_pull_request.return_value = _canonical_pull_request()
    handler = AsyncMock()
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=handle,
        handler=handler,
    )
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")
    client = TestClient(
        create_app(
            _settings(),
            webhook_secret=_SECRET,
            dispatcher=dispatcher,
            delivery_store=store,
        )
    )
    payload = _body(
        {
            "repository": {"full_name": "o/r"},
            "action": "created",
            "issue": {
                "number": 42,
                "pull_request": {"url": "https://attacker.invalid/pulls/999"},
            },
            "comment": {
                "id": 100,
                "user": {"login": "alice", "id": 1},
                "body": "/openrabbit summary",
                "url": "https://attacker.invalid/comments/100",
                "html_url": "https://github.com/o/r/pull/42#issuecomment-100",
                "created_at": "2026-08-01T01:00:00Z",
                "updated_at": "2026-08-01T01:00:00Z",
            },
        }
    )

    response = client.post(
        GITHUB_WEBHOOK_PATH,
        content=payload,
        headers=_headers(payload, event="issue_comment"),
    )

    assert response.status_code == 202
    handle.get_pull_request.assert_awaited_once_with(42)
    handler.assert_awaited_once()


def _canonical_pull_request() -> PullRequest:
    return PullRequest.model_validate(_pull_request_payload()["pull_request"])
