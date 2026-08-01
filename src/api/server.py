"""Optional FastAPI application for GitHub webhook intake."""

from __future__ import annotations

import json
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from configs import Settings
from github_ import (
    is_webhook_event_allowed,
    is_webhook_payload_within_limit,
    verify_github_webhook_signature,
)

HEALTH_PATH = "/health"
GITHUB_WEBHOOK_PATH = "/webhooks/github"
MAX_DELIVERY_ID_LENGTH = 128

_LOGGER = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Small public readiness response without workspace details."""

    status: Literal["ok"] = "ok"
    service: Literal["openrabbit"] = "openrabbit"
    version: str
    webhook_enabled: bool


class WebhookAcceptedResponse(BaseModel):
    """Receipt returned before shared dispatch is added in OP-144."""

    status: Literal["accepted"] = "accepted"
    event: str
    delivery_id: str | None
    dispatched: Literal[False] = False


def create_app(
    settings: Settings,
    *,
    webhook_secret: str | bytes | None = None,
) -> FastAPI:
    """Create an application bound to validated OpenRabbit settings."""
    app = FastAPI(
        title="OpenRabbit Webhook Server",
        version=_package_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get(HEALTH_PATH, response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            version=app.version,
            webhook_enabled=settings.webhook.enabled,
        )

    @app.post(
        GITHUB_WEBHOOK_PATH,
        response_model=WebhookAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def github_webhook(request: Request) -> WebhookAcceptedResponse:
        if not settings.webhook.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub webhook mode is disabled.",
            )
        if not webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub webhook secret is unavailable.",
            )

        _reject_oversized_content_length(
            request.headers.get("content-length"),
            settings.webhook.max_payload_bytes,
        )
        payload = await _read_bounded_payload(
            request,
            settings.webhook.max_payload_bytes,
        )

        signature = request.headers.get("x-hub-signature-256")
        if not verify_github_webhook_signature(payload, signature, webhook_secret):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="GitHub webhook signature is invalid.",
            )

        event_name = _normalized_event_name(request.headers.get("x-github-event"))
        if event_name is None or not is_webhook_event_allowed(
            event_name,
            settings.webhook.allowed_events,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub webhook event is missing or unsupported.",
            )

        delivery_id = _validated_delivery_id(request.headers.get("x-github-delivery"))
        _parse_payload_object(payload)
        _LOGGER.info(
            "Accepted GitHub webhook delivery event=%s delivery_id=%s",
            event_name,
            delivery_id or "missing",
        )
        return WebhookAcceptedResponse(
            event=event_name,
            delivery_id=delivery_id,
        )

    return app


def _package_version() -> str:
    try:
        return version("openrabbit")
    except PackageNotFoundError:  # pragma: no cover - source checkout fallback
        return "0.0.0+local"


def _reject_oversized_content_length(value: str | None, maximum: int) -> None:
    if value is None:
        return
    try:
        content_length = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length must be a non-negative integer.",
        ) from exc
    if content_length < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length must be a non-negative integer.",
        )
    if content_length > maximum:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="GitHub webhook payload exceeds the configured limit.",
        )


async def _read_bounded_payload(request: Request, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        if chunk and not is_webhook_payload_within_limit(chunk, maximum - size):
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="GitHub webhook payload exceeds the configured limit.",
            )
        size += len(chunk)
        chunks.append(chunk)
    return b"".join(chunks)


def _normalized_event_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _validated_delivery_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_DELIVERY_ID_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub delivery ID exceeds the supported length.",
        )
    return normalized


def _parse_payload_object(payload: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub webhook payload must be valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub webhook payload must be a JSON object.",
        )
    return parsed
