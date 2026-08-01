"""Tests for the optional FastAPI webhook intake surface."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from api import GITHUB_WEBHOOK_PATH, HEALTH_PATH, create_app
from configs import Settings, WebhookSettings

_SECRET = "test-webhook-secret"


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
