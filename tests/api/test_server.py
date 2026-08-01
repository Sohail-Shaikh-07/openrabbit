"""Tests for the optional FastAPI webhook intake surface."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api import GITHUB_WEBHOOK_PATH, HEALTH_PATH, WebhookEventDispatcher, create_app
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
