"""Optional FastAPI application for GitHub webhook intake."""

from __future__ import annotations

import json
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from api.delivery_state import (
    DeliveryStateCapacityError,
    DeliveryStateConflict,
    DeliveryStateError,
    SQLiteDeliveryStateStore,
)
from api.dispatch import (
    WebhookDispatchRequest,
    WebhookEventDispatcher,
    WebhookPayloadError,
)
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
    """Receipt returned after webhook work is validated and scheduled."""

    status: Literal["accepted"] = "accepted"
    event: str
    delivery_id: str | None
    dispatched: bool = False
    reason: str | None = None


def create_app(
    settings: Settings,
    *,
    webhook_secret: str | bytes | None = None,
    dispatcher: WebhookEventDispatcher | None = None,
    delivery_store: SQLiteDeliveryStateStore | None = None,
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
        response_model_exclude_none=True,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> WebhookAcceptedResponse:
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
        parsed_payload = _parse_payload_object(payload)
        if dispatcher is None:
            dispatch_plan = None
        else:
            try:
                dispatch_plan = dispatcher.prepare(event_name, parsed_payload)
            except WebhookPayloadError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
        if dispatch_plan is None:
            _log_accepted_delivery(event_name, delivery_id)
            return WebhookAcceptedResponse(
                event=event_name,
                delivery_id=delivery_id,
            )
        delivery_claim = None
        if delivery_store is not None and dispatch_plan.request is not None:
            if delivery_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Actionable GitHub webhooks require a delivery ID.",
                )
            try:
                delivery_claim = delivery_store.claim(
                    delivery_id=delivery_id,
                    event_name=event_name,
                    repository=dispatch_plan.repository,
                    pr_number=dispatch_plan.request.pr_number,
                    operation=_delivery_operation(dispatch_plan.request),
                )
            except DeliveryStateConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc
            except DeliveryStateCapacityError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc
            except DeliveryStateError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Webhook delivery state is unavailable.",
                ) from exc
        elif delivery_store is not None and delivery_id is not None:
            try:
                delivery_claim = delivery_store.record_ignored(
                    delivery_id=delivery_id,
                    event_name=event_name,
                    repository=dispatch_plan.repository,
                    pr_number=_optional_pr_number(parsed_payload),
                    operation=dispatch_plan.reason,
                    reason=dispatch_plan.reason,
                )
            except DeliveryStateConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc
            except DeliveryStateCapacityError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc
            except DeliveryStateError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Webhook delivery state is unavailable.",
                ) from exc
        _log_accepted_delivery(event_name, delivery_id)
        if dispatch_plan.request is None:
            return WebhookAcceptedResponse(
                event=event_name,
                delivery_id=delivery_id,
                reason=(delivery_claim.reason if delivery_claim else dispatch_plan.reason),
            )
        if delivery_claim is not None and not delivery_claim.claimed:
            return WebhookAcceptedResponse(
                event=event_name,
                delivery_id=delivery_id,
                reason=delivery_claim.reason,
            )
        assert dispatcher is not None
        background_tasks.add_task(
            _dispatch_safely,
            dispatcher,
            dispatch_plan.request,
            delivery_id,
            delivery_store,
        )
        return WebhookAcceptedResponse(
            event=event_name,
            delivery_id=delivery_id,
            dispatched=True,
            reason=(delivery_claim.reason if delivery_claim else dispatch_plan.reason),
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


async def _dispatch_safely(
    dispatcher: WebhookEventDispatcher,
    request: WebhookDispatchRequest,
    delivery_id: str | None,
    delivery_store: SQLiteDeliveryStateStore | None = None,
) -> None:
    try:
        await dispatcher.dispatch(request)
    except Exception as exc:
        if delivery_store is not None and delivery_id is not None:
            try:
                delivery_store.mark_failed(delivery_id, error_type=type(exc).__name__)
            except DeliveryStateError as state_exc:
                _log_delivery_state_failure(
                    delivery_id=delivery_id,
                    operation="mark_failed",
                    error=state_exc,
                )
        _LOGGER.error(
            "GitHub webhook dispatch failed event_repo=%s pr=%s delivery_id=%s error_type=%s",
            request.repository,
            request.pr_number,
            delivery_id or "missing",
            type(exc).__name__,
        )
    else:
        if delivery_store is not None and delivery_id is not None:
            try:
                delivery_store.mark_succeeded(delivery_id)
            except DeliveryStateError as exc:
                _log_delivery_state_failure(
                    delivery_id=delivery_id,
                    operation="mark_succeeded",
                    error=exc,
                )


def _delivery_operation(request: WebhookDispatchRequest) -> str:
    if request.event_kind is not None:
        return request.event_kind
    if request.source_comment_id is not None:
        return f"issue_comment:{request.source_comment_id}"
    return "pull_request_updated"


def _log_accepted_delivery(event_name: str, delivery_id: str | None) -> None:
    _LOGGER.info(
        "Accepted GitHub webhook delivery event=%s delivery_id=%s",
        event_name,
        delivery_id or "missing",
    )


def _optional_pr_number(payload: dict[str, object]) -> int | None:
    for key in ("pull_request", "issue"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        number = value.get("number")
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            return number
    return None


def _log_delivery_state_failure(
    *,
    delivery_id: str,
    operation: str,
    error: DeliveryStateError,
) -> None:
    _LOGGER.error(
        "GitHub webhook delivery state update failed delivery_id=%s operation=%s error_type=%s",
        delivery_id,
        operation,
        type(error).__name__,
    )
