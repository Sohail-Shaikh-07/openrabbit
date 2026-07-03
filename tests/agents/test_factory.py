"""Tests for building configured review agents."""

from __future__ import annotations

import pytest

from agents.factory import UnsupportedModelProviderError, build_llm_client, build_review_agents
from agents.llm import OllamaClient
from configs.schema import ModelSettings, ReviewSettings
from configs.settings import Settings


def test_build_llm_client_returns_ollama_contract_client() -> None:
    client = build_llm_client(ModelSettings(provider="ollama", model_name="qwen2.5-coder:7b"))

    assert isinstance(client, OllamaClient)
    assert client.provider_name == "ollama"
    assert client.model_name == "qwen2.5-coder:7b"


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
    assert agents[0]._client.model_name == "openrabbit-reviewer-v1"


def test_build_review_agents_reuses_one_model_client_for_enabled_agents() -> None:
    settings = Settings(
        review=ReviewSettings(
            security=True,
            performance=True,
            architecture=True,
            bug=False,
            test_coverage=False,
        ),
        model=ModelSettings(provider="ollama", model_name="openrabbit-reviewer-v1"),
    )

    agents = build_review_agents(settings)

    assert len({id(agent._client) for agent in agents}) == 1
    assert {agent._client.provider_name for agent in agents} == {"ollama"}


def test_build_review_agents_rejects_unimplemented_provider() -> None:
    settings = Settings(model=ModelSettings(provider="transformers"))

    with pytest.raises(UnsupportedModelProviderError, match="transformers"):
        build_review_agents(settings)
