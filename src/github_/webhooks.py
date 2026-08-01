"""Fail-closed helpers for GitHub webhook intake."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Collection

GITHUB_SIGNATURE_PREFIX = "sha256="
GITHUB_SIGNATURE_HEX_LENGTH = hashlib.sha256().digest_size * 2


def verify_github_webhook_signature(
    payload: bytes,
    signature_header: str | None,
    secret: str | bytes | None,
) -> bool:
    """Return whether a GitHub ``X-Hub-Signature-256`` value is valid."""
    if not signature_header or not secret:
        return False

    signature = signature_header.strip()
    if not signature.startswith(GITHUB_SIGNATURE_PREFIX):
        return False
    encoded_digest = signature.removeprefix(GITHUB_SIGNATURE_PREFIX)
    if len(encoded_digest) != GITHUB_SIGNATURE_HEX_LENGTH:
        return False
    try:
        supplied_digest = bytes.fromhex(encoded_digest)
    except ValueError:
        return False

    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    expected_digest = hmac.new(secret_bytes, payload, hashlib.sha256).digest()
    return hmac.compare_digest(expected_digest, supplied_digest)


def is_webhook_event_allowed(
    event_name: str | None,
    allowed_events: Collection[str],
) -> bool:
    """Return whether a normalized GitHub event is explicitly allowed."""
    if not event_name:
        return False
    normalized = event_name.strip().lower()
    return bool(normalized) and normalized in allowed_events


def is_webhook_payload_within_limit(payload: bytes, max_payload_bytes: int) -> bool:
    """Return whether a payload can be accepted without exceeding the configured bound."""
    return max_payload_bytes > 0 and len(payload) <= max_payload_bytes
