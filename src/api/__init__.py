"""FastAPI HTTP surface for optional OpenRabbit server mode."""

from __future__ import annotations

from api.dispatch import (
    WebhookDispatchPlan,
    WebhookDispatchRequest,
    WebhookEventDispatcher,
    WebhookPayloadError,
    map_webhook_event,
)
from api.server import (
    GITHUB_WEBHOOK_PATH,
    HEALTH_PATH,
    HealthResponse,
    WebhookAcceptedResponse,
    create_app,
)

__all__ = [
    "GITHUB_WEBHOOK_PATH",
    "HEALTH_PATH",
    "HealthResponse",
    "WebhookAcceptedResponse",
    "WebhookDispatchPlan",
    "WebhookDispatchRequest",
    "WebhookEventDispatcher",
    "WebhookPayloadError",
    "create_app",
    "map_webhook_event",
]
