"""Tests for building configured review agents."""

from __future__ import annotations

import pytest

from agents.factory import build_review_agents
from configs.schema import ModelSettings, ReviewSettings
from configs.settings import Settings


def test_build_review_agents_honors_enabled_agents_and_model_name() -> None:
    settings = Settings(
        review=ReviewSettings(
            security=True,
            performance=False,
            architecture=False,
            bug=False,
            test_coverage=False,
        ),
        model=ModelSettings(provider="ollama", model_name="openrabbit-reviewer-v1"),
    )

    agents = build_review_agents(settings)

    assert [agent.name for agent in agents] == ["security"]
    assert agents[0]._client._model == "openrabbit-reviewer-v1"


def test_build_review_agents_rejects_unimplemented_provider() -> None:
    settings = Settings(model=ModelSettings(provider="transformers"))

    with pytest.raises(NotImplementedError, match="transformers"):
        build_review_agents(settings)
