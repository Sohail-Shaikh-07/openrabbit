"""Runtime entrypoint for optional FastAPI webhook server mode."""

from __future__ import annotations

import asyncio

import uvicorn

from api import (
    DeliveryStateError,
    SQLiteDeliveryStateStore,
    WebhookEventDispatcher,
    create_app,
    delivery_state_path,
)
from cli.commands.start import build_workspace_review_handler
from configs import Settings
from github_ import GitHubAuthError, GitHubClient, RepositoryHandle


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

    target_repository = settings.repository.target
    if not target_repository:
        raise ServerError("repository.target is required before webhook dispatch can be enabled")
    try:
        client = GitHubClient.from_settings(settings)
    except GitHubAuthError as exc:
        raise ServerError(str(exc)) from exc
    try:
        handle = RepositoryHandle.from_full_name(target_repository, client)
        handler = build_workspace_review_handler(
            settings,
            workspace=settings.resolved_workspace_root(),
        )
        dispatcher = WebhookEventDispatcher(
            expected_repository=target_repository,
            handle=handle,
            handler=handler,
        )
        try:
            delivery_store = SQLiteDeliveryStateStore(
                delivery_state_path(settings.resolved_workspace_root())
            )
        except DeliveryStateError as exc:
            raise ServerError("webhook delivery state could not be initialized") from exc
        uvicorn.run(
            create_app(
                settings,
                webhook_secret=webhook_secret,
                dispatcher=dispatcher,
                delivery_store=delivery_store,
            ),
            host=normalized_host,
            port=port,
        )
    finally:
        asyncio.run(client.aclose())
