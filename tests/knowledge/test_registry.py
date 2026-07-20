from __future__ import annotations

from configs import Settings
from configs.schema import KnowledgeSettings
from knowledge.registry import build_connector_registry


def test_registry_lists_default_connectors_as_disabled() -> None:
    registry = build_connector_registry(Settings())

    health = registry.check_health(env={})

    assert [item.name for item in health] == ["mcp", "web_search", "multi_repo", "jira", "linear"]
    assert all(item.enabled is False for item in health)
    assert all(item.available is False for item in health)
    assert all(item.reason == "disabled" for item in health)


def test_registry_reports_jira_missing_token_without_leaking_env_value() -> None:
    settings = Settings(
        knowledge=KnowledgeSettings.model_validate(
            {
                "connectors": {
                    "jira": {
                        "enabled": True,
                        "base_url": "https://example.atlassian.net",
                        "token_env": "TEAM_JIRA_TOKEN",
                    }
                }
            }
        )
    )
    registry = build_connector_registry(settings)

    health = {item.name: item for item in registry.check_health(env={})}

    assert health["jira"].enabled is True
    assert health["jira"].available is False
    assert "TEAM_JIRA_TOKEN" in health["jira"].reason


def test_registry_reports_enabled_linear_as_configured_when_token_env_exists() -> None:
    settings = Settings(
        knowledge=KnowledgeSettings.model_validate(
            {"connectors": {"linear": {"enabled": True, "token_env": "TEAM_LINEAR_TOKEN"}}}
        )
    )
    registry = build_connector_registry(settings)

    health = {
        item.name: item for item in registry.check_health(env={"TEAM_LINEAR_TOKEN": "secret"})
    }

    assert health["linear"].enabled is True
    assert health["linear"].available is True
    assert health["linear"].reason == "configured"
