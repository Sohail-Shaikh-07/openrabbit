"""Connector registry health checks for the OpenRabbit CLI."""

from __future__ import annotations

from dataclasses import dataclass

from configs.settings import Settings
from knowledge.registry import build_connector_registry


@dataclass(frozen=True)
class ConnectorHealthResult:
    """User-facing connector health row."""

    name: str
    enabled: bool
    available: bool
    source_kind: str
    reason: str


def run_connector_health_check(
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
) -> list[ConnectorHealthResult]:
    """Return read-only connector health without contacting remote services."""
    registry = build_connector_registry(settings)
    return [
        ConnectorHealthResult(
            name=item.name,
            enabled=item.enabled,
            available=item.available,
            source_kind=item.source_kind.value,
            reason=item.reason,
        )
        for item in registry.check_health(env=env)
    ]
