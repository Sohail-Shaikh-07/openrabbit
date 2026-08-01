"""FastAPI HTTP surface for optional OpenRabbit server mode."""

from __future__ import annotations

from api.delivery_state import (
    DEFAULT_PROCESSING_TIMEOUT,
    DEFAULT_RETENTION_LIMIT,
    DELIVERY_STATE_FILENAME,
    DeliveryClaim,
    DeliveryRecord,
    DeliveryStateCapacityError,
    DeliveryStateConflict,
    DeliveryStateError,
    SQLiteDeliveryStateStore,
    delivery_state_path,
)
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
    "DEFAULT_PROCESSING_TIMEOUT",
    "DEFAULT_RETENTION_LIMIT",
    "DELIVERY_STATE_FILENAME",
    "GITHUB_WEBHOOK_PATH",
    "HEALTH_PATH",
    "DeliveryClaim",
    "DeliveryRecord",
    "DeliveryStateCapacityError",
    "DeliveryStateConflict",
    "DeliveryStateError",
    "HealthResponse",
    "SQLiteDeliveryStateStore",
    "WebhookAcceptedResponse",
    "WebhookDispatchPlan",
    "WebhookDispatchRequest",
    "WebhookEventDispatcher",
    "WebhookPayloadError",
    "create_app",
    "delivery_state_path",
    "map_webhook_event",
]
