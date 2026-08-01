"""Tests for the optional FastAPI webhook intake surface."""

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
    HEALTH_PATH,
    DeliveryStateError,
    SQLiteDeliveryStateStore,
    WebhookEventDispatcher,
    create_app,
)
from configs import Settings, WebhookSettings
from github_ import RepositoryHandle

_SECRET = "test-webhook-secret"


def _pull_request_payload() -> dict[str, object]:
    return {
        "number": 42,
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


def _actionable_payload() -> dict[str, object]:
    return {
        "repository": {"full_name": "o/r"},
        "action": "opened",
        "pull_request": _pull_request_payload(),
    }


def _settings(*, enabled: bool = True, max_payload_bytes: int = 1_024) -> Settings:
    return Settings(
        webhook=WebhookSettings(
            enabled=enabled,
            secret_env="TEST_WEBHOOK_SECRET",
            max_payload_bytes=max_payload_bytes,
        )
    )


def _signature(payload: bytes, secret: str = _SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _headers(
    payload: bytes,
    *,
    event: str = "pull_request",
    delivery_id: str = "67a0b0f4-0b89-4c4f-8f75-d668c2574531",
    secret: str = _SECRET,
) -> dict[str, str]:
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": _signature(payload, secret),
        "Content-Type": "application/json",
    }


def test_health_returns_public_readiness_without_workspace_details() -> None:
    client = TestClient(create_app(_settings(enabled=False)))

    response = client.get(HEALTH_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "openrabbit"
    assert body["version"]
    assert body["webhook_enabled"] is False
    assert "secret" not in response.text.lower()
    assert "workspace" not in response.text.lower()


def test_github_webhook_accepts_valid_delivery_without_dispatching() -> None:
    payload = b'{"action":"opened","number":42}'
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(
        GITHUB_WEBHOOK_PATH,
        content=payload,
        headers=_headers(payload, event=" Pull_Request "),
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "event": "pull_request",
        "delivery_id": "67a0b0f4-0b89-4c4f-8f75-d668c2574531",
        "dispatched": False,
    }


def test_github_webhook_schedules_actionable_shared_dispatch() -> None:
    payload = json.dumps(
        _actionable_payload(),
        separators=(",", ":"),
    ).encode()
    handler = AsyncMock()
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=handler,
    )
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET, dispatcher=dispatcher))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))

    assert response.status_code == 202
    assert response.json()["dispatched"] is True
    assert response.json()["reason"] == "scheduled"
    handler.assert_awaited_once()


def test_delivery_state_suppresses_completed_actionable_duplicate(tmp_path: Path) -> None:
    payload = json.dumps(_actionable_payload(), separators=(",", ":")).encode()
    handler = AsyncMock()
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
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

    first = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))
    duplicate = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))

    assert first.status_code == 202
    assert first.json()["dispatched"] is True
    assert first.json()["reason"] == "scheduled"
    assert duplicate.status_code == 202
    assert duplicate.json()["dispatched"] is False
    assert duplicate.json()["reason"] == "duplicate_succeeded"
    handler.assert_awaited_once()
    record = store.get("67a0b0f4-0b89-4c4f-8f75-d668c2574531")
    assert record is not None
    assert record.status == "succeeded"


def test_failed_delivery_can_be_redelivered(tmp_path: Path) -> None:
    payload = json.dumps(_actionable_payload(), separators=(",", ":")).encode()
    handler = AsyncMock(side_effect=[RuntimeError("private detail"), None])
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
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

    failed = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))
    failed_record = store.get("67a0b0f4-0b89-4c4f-8f75-d668c2574531")
    retried = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))
    completed_record = store.get("67a0b0f4-0b89-4c4f-8f75-d668c2574531")

    assert failed.status_code == 202
    assert failed_record is not None
    assert failed_record.status == "failed"
    assert failed_record.last_error_type == "RuntimeError"
    assert "private detail" not in str(failed_record)
    assert retried.status_code == 202
    assert retried.json()["dispatched"] is True
    assert retried.json()["reason"] == "retry_scheduled"
    assert completed_record is not None
    assert completed_record.status == "succeeded"
    assert completed_record.attempts == 2
    assert handler.await_count == 2


def test_actionable_delivery_requires_id_when_state_is_enabled(tmp_path: Path) -> None:
    payload = json.dumps(_actionable_payload(), separators=(",", ":")).encode()
    headers = _headers(payload)
    headers.pop("X-GitHub-Delivery")
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=AsyncMock(),
    )
    client = TestClient(
        create_app(
            _settings(),
            webhook_secret=_SECRET,
            dispatcher=dispatcher,
            delivery_store=SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3"),
        )
    )

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 400
    assert "delivery ID" in response.json()["detail"]


def test_ignored_delivery_is_persisted_and_deduplicated(tmp_path: Path) -> None:
    payload = b'{"repository":{"full_name":"o/r"}}'
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=AsyncMock(),
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
    headers = _headers(payload, event="ping", delivery_id="ping-delivery")

    first = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)
    duplicate = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert first.status_code == 202
    assert first.json()["reason"] == "ping"
    assert duplicate.status_code == 202
    assert duplicate.json()["reason"] == "duplicate_ignored"
    record = store.get("ping-delivery")
    assert record is not None
    assert record.status == "ignored"


def test_conflicting_delivery_reuse_returns_conflict(tmp_path: Path) -> None:
    ping_payload = b'{"repository":{"full_name":"o/r"}}'
    action_payload = json.dumps(_actionable_payload(), separators=(",", ":")).encode()
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=AsyncMock(),
    )
    client = TestClient(
        create_app(
            _settings(),
            webhook_secret=_SECRET,
            dispatcher=dispatcher,
            delivery_store=SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3"),
        )
    )
    delivery_id = "reused-delivery"
    first = client.post(
        GITHUB_WEBHOOK_PATH,
        content=ping_payload,
        headers=_headers(ping_payload, event="ping", delivery_id=delivery_id),
    )

    conflict = client.post(
        GITHUB_WEBHOOK_PATH,
        content=action_payload,
        headers=_headers(action_payload, delivery_id=delivery_id),
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert "conflicting" in conflict.json()["detail"]


def test_unavailable_delivery_state_returns_safe_retryable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(_actionable_payload(), separators=(",", ":")).encode()
    handler = AsyncMock()
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=handler,
    )
    store = SQLiteDeliveryStateStore(tmp_path / "deliveries.sqlite3")

    def fail_claim(**kwargs: object) -> None:
        raise DeliveryStateError("private database path")

    monkeypatch.setattr(store, "claim", fail_claim)
    client = TestClient(
        create_app(
            _settings(),
            webhook_secret=_SECRET,
            dispatcher=dispatcher,
            delivery_store=store,
        )
    )

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))

    assert response.status_code == 503
    assert response.json()["detail"] == "Webhook delivery state is unavailable."
    assert "private database path" not in response.text
    handler.assert_not_awaited()


def test_github_webhook_rejects_repository_mismatch_before_dispatch() -> None:
    payload = json.dumps(
        _actionable_payload(),
        separators=(",", ":"),
    ).encode()
    dispatcher = WebhookEventDispatcher(
        expected_repository="other/repo",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=AsyncMock(),
    )
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET, dispatcher=dispatcher))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))

    assert response.status_code == 400
    assert "repository.target" in response.json()["detail"]


def test_background_dispatch_failure_does_not_log_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = json.dumps(_actionable_payload(), separators=(",", ":")).encode()
    handler = AsyncMock(side_effect=RuntimeError("sensitive-payload-content"))
    dispatcher = WebhookEventDispatcher(
        expected_repository="o/r",
        handle=AsyncMock(spec=RepositoryHandle),
        handler=handler,
    )
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET, dispatcher=dispatcher))

    with caplog.at_level(logging.ERROR, logger="api.server"):
        response = client.post(
            GITHUB_WEBHOOK_PATH,
            content=payload,
            headers=_headers(payload),
        )

    assert response.status_code == 202
    assert "RuntimeError" in caplog.text
    assert "sensitive-payload-content" not in caplog.text


def test_github_webhook_rejects_disabled_mode() -> None:
    payload = b'{"zen":"hello"}'
    client = TestClient(create_app(_settings(enabled=False), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))

    assert response.status_code == 503
    assert response.json()["detail"] == "GitHub webhook mode is disabled."


def test_github_webhook_rejects_unavailable_secret() -> None:
    payload = b'{"zen":"hello"}'
    client = TestClient(create_app(_settings()))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))

    assert response.status_code == 503
    assert response.json()["detail"] == "GitHub webhook secret is unavailable."


@pytest.mark.parametrize("signature", [None, "", "sha1=abc", "sha256=abc"])
def test_github_webhook_rejects_missing_or_malformed_signature(
    signature: str | None,
) -> None:
    payload = b'{"action":"opened"}'
    headers = _headers(payload)
    if signature is None:
        headers.pop("X-Hub-Signature-256")
    else:
        headers["X-Hub-Signature-256"] = signature
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"] == "GitHub webhook signature is invalid."


def test_signature_is_checked_before_event_or_json_processing() -> None:
    payload = b"not-json"
    headers = _headers(payload, event="push", secret="wrong-secret")
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 401


@pytest.mark.parametrize("event", ["", "push"])
def test_github_webhook_rejects_missing_or_unsupported_event(event: str) -> None:
    payload = b'{"action":"opened"}'
    headers = _headers(payload, event=event)
    if not event:
        headers.pop("X-GitHub-Event")
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub webhook event is missing or unsupported."


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'"string"'])
def test_github_webhook_rejects_invalid_or_non_object_json(payload: bytes) -> None:
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=_headers(payload))

    assert response.status_code == 400
    assert "JSON" in response.json()["detail"]


def test_github_webhook_rejects_oversized_content_length_before_reading() -> None:
    payload = b"{}"
    headers = _headers(payload)
    headers["Content-Length"] = "1025"
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 413


@pytest.mark.parametrize("content_length", ["invalid", "-1"])
def test_github_webhook_rejects_invalid_content_length(content_length: str) -> None:
    payload = b"{}"
    headers = _headers(payload)
    headers["Content-Length"] = content_length
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 400


def test_github_webhook_rejects_payload_over_actual_bound() -> None:
    payload = b"{" + (b'"x":' + b'"' + (b"a" * 1_024) + b'"') + b"}"
    headers = _headers(payload)
    headers.pop("Content-Length", None)
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 413


def test_github_webhook_stream_bound_rejects_deceptive_content_length() -> None:
    payload = b"{" + (b'"x":' + b'"' + (b"a" * 1_024) + b'"') + b"}"
    headers = _headers(payload)
    headers["Content-Length"] = "1"
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 413


def test_github_webhook_rejects_unbounded_delivery_id() -> None:
    payload = b"{}"
    headers = _headers(payload, delivery_id="d" * 129)
    client = TestClient(create_app(_settings(), webhook_secret=_SECRET))

    response = client.post(GITHUB_WEBHOOK_PATH, content=payload, headers=headers)

    assert response.status_code == 400
    assert "delivery ID" in response.json()["detail"]
