"""Model provider health checks for the OpenRabbit CLI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from agents.factory import build_llm_client
from agents.llm import LLMClient
from configs.schema import ModelSettings
from configs.settings import Settings

_HEALTH_PROMPT = (
    'Return exactly this JSON object and nothing else: {"ok": true, "message": "ready"}'
)


class ClientFactory(Protocol):
    def __call__(self, model: ModelSettings, *, api_key: str | None = None) -> LLMClient: ...


@dataclass(frozen=True)
class ModelHealthResult:
    """User-facing health check result for the configured model provider."""

    ok: bool
    provider: str
    model: str
    message: str


async def run_model_health_check(
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
    client_factory: ClientFactory = build_llm_client,
) -> ModelHealthResult:
    """Check whether the configured model provider can generate a response."""
    provider = settings.model.provider
    model_name = settings.model.model_name
    try:
        client = client_factory(
            settings.model,
            api_key=(
                settings.resolved_model_api_key(env=env)
                if settings.model.provider != "ollama"
                else None
            ),
        )
        raw = (await client.generate(_HEALTH_PROMPT)).strip()
    except Exception as exc:
        return ModelHealthResult(
            ok=False,
            provider=provider,
            model=model_name,
            message=f"Model provider health check failed: {exc}",
        )
    if not raw:
        return ModelHealthResult(
            ok=False,
            provider=client.provider_name,
            model=client.model_name,
            message="Model provider returned an empty response.",
        )
    return ModelHealthResult(
        ok=True,
        provider=client.provider_name,
        model=client.model_name,
        message="Model provider reachable.",
    )


def run_model_health_check_blocking(
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
    client_factory: ClientFactory = build_llm_client,
) -> ModelHealthResult:
    """Synchronous wrapper for Typer commands and tests."""
    return asyncio.run(run_model_health_check(settings, env=env, client_factory=client_factory))
