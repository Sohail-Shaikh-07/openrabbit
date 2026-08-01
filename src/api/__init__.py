"""FastAPI HTTP surface for optional OpenRabbit server mode."""

from __future__ import annotations

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
    "create_app",
]
