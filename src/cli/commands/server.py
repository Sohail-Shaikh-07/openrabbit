"""Runtime entrypoint for optional FastAPI webhook server mode."""

from __future__ import annotations

import uvicorn

from api import create_app
from configs import Settings


class ServerError(RuntimeError):
    """Raised when webhook server mode cannot start safely."""


def run_server(settings: Settings, *, host: str, port: int) -> None:
    """Validate server mode and run Uvicorn in the foreground."""
    normalized_host = host.strip()
    if not normalized_host:
        raise ServerError("server host must not be empty")
    if not 1 <= port <= 65_535:
        raise ServerError("server port must be between 1 and 65535")
    if not settings.webhook.enabled:
        raise ServerError(
            "webhook mode is disabled; set webhook.enabled to true before starting the server"
        )
    webhook_secret = settings.resolved_webhook_secret()
    if not webhook_secret:
        raise ServerError(
            f"webhook secret is unavailable; set {settings.webhook.secret_env} in the environment"
        )

    uvicorn.run(
        create_app(settings, webhook_secret=webhook_secret),
        host=normalized_host,
        port=port,
    )
