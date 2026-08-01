"""Tests for GitHub webhook intake helpers."""

from __future__ import annotations

import hashlib
import hmac
import subprocess
import sys

import pytest

from github_ import (
    is_webhook_event_allowed,
    is_webhook_payload_within_limit,
    verify_github_webhook_signature,
)


def test_github_package_imports_in_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from github_ import verify_github_webhook_signature",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_github_webhook_signature_accepts_valid_digest() -> None:
    payload = b'{"zen":"Keep it logically awesome."}'
    signature = _signature(payload, "test-secret")

    assert verify_github_webhook_signature(payload, signature, "test-secret")
    assert verify_github_webhook_signature(payload, signature.upper(), b"test-secret") is False
    assert verify_github_webhook_signature(
        payload,
        f"sha256={signature.removeprefix('sha256=').upper()}",
        b"test-secret",
    )


def test_verify_github_webhook_signature_rejects_changed_input() -> None:
    payload = b'{"action":"opened"}'
    signature = _signature(payload, "test-secret")

    assert not verify_github_webhook_signature(b'{"action":"closed"}', signature, "test-secret")
    assert not verify_github_webhook_signature(payload, signature, "wrong-secret")


@pytest.mark.parametrize(
    "signature,secret",
    [
        (None, "test-secret"),
        ("", "test-secret"),
        ("sha1=abc", "test-secret"),
        ("sha256=abc", "test-secret"),
        (f"sha256={'z' * 64}", "test-secret"),
        (_signature(b"payload", "test-secret"), None),
        (_signature(b"payload", "test-secret"), ""),
    ],
)
def test_verify_github_webhook_signature_fails_closed(
    signature: str | None,
    secret: str | None,
) -> None:
    assert not verify_github_webhook_signature(b"payload", signature, secret)


def test_webhook_event_allowlist_is_explicit() -> None:
    allowed = ["ping", "pull_request", "issue_comment"]

    assert is_webhook_event_allowed("pull_request", allowed)
    assert is_webhook_event_allowed(" Issue_Comment ", allowed)
    assert not is_webhook_event_allowed("push", allowed)
    assert not is_webhook_event_allowed(None, allowed)


def test_webhook_payload_limit_includes_exact_boundary() -> None:
    assert is_webhook_payload_within_limit(b"1234", 4)
    assert not is_webhook_payload_within_limit(b"12345", 4)
    assert not is_webhook_payload_within_limit(b"", 0)
