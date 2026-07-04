"""Tests for building configured review agents."""

from __future__ import annotations

import pytest

from agents.factory import (
    MissingModelAPIKeyError,
    MissingModelBaseURLError,
    UnsupportedModelProviderError,
    build_llm_client,
    build_review_agents,
)
from agents.llm import OllamaClient, OpenAIClient, OpenAICompatibleClient
from configs.schema import ModelSettings, ReviewSettings
from configs.settings import Settings


def test_build_llm_client_returns_ollama_contract_client() -> None:
    client = build_llm_client(ModelSettings(provider="ollama", model_name="qwen2.5-coder:7b"))

    assert isinstance(client, OllamaClient)
    assert client.provider_name == "ollama"
    assert client.model_name == "qwen2.5-coder:7b"


def test_build_llm_client_returns_openai_contract_client() -> None:
    client = build_llm_client(
        ModelSettings(provider="openai", model_name="gpt-4.1-mini"),
        api_key="sk-test",
    )

    assert isinstance(client, OpenAIClient)
    assert client.provider_name == "openai"
    assert client.model_name == "gpt-4.1-mini"


def test_build_llm_client_returns_openai_compatible_contract_client() -> None:
    client = build_llm_client(
        ModelSettings(
            provider="openai-compatible",
            model_name="openai/gpt-oss-20b",
            base_url="http://localhost:8000/v1",
            api_key_env="OPENAI_COMPATIBLE_API_KEY",
        ),
        api_key="local-key",
    )

    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider_name == "openai-compatible"
    assert client.model_name == "openai/gpt-oss-20b"


def test_build_llm_client_requires_openai_api_key() -> None:
    model = ModelSettings(
        provider="openai", model_name="gpt-4.1-mini", api_key_env="OPENAI_API_KEY"
    )

    with pytest.raises(MissingModelAPIKeyError, match="OPENAI_API_KEY") as exc:
        build_llm_client(model)

    assert "sk-" not in str(exc.value)


def test_build_llm_client_requires_compatible_base_url() -> None:
    model = ModelSettings(
        provider="openai-compatible",
        model_name="openai/gpt-oss-20b",
        api_key_env="OPENAI_COMPATIBLE_API_KEY",
    )

    with pytest.raises(MissingModelBaseURLError, match=r"model\.base_url"):
        build_llm_client(model, api_key="local-key")


def test_build_llm_client_requires_compatible_api_key() -> None:
    model = ModelSettings(
        provider="openai-compatible",
        model_name="openai/gpt-oss-20b",
        base_url="http://localhost:8000/v1",
        api_key_env="OPENAI_COMPATIBLE_API_KEY",
    )

    with pytest.raises(MissingModelAPIKeyError, match="OPENAI_COMPATIBLE_API_KEY") as exc:
        build_llm_client(model)

    assert "local-key" not in str(exc.value)


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


def test_build_review_agents_wires_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings(
        review=ReviewSettings(
            security=True,
            performance=False,
            architecture=False,
            bug=False,
            test_coverage=False,
        ),
        model=ModelSettings(provider="openai", model_name="gpt-4.1-mini"),
    )

    agents = build_review_agents(settings)

    assert [agent.name for agent in agents] == ["security"]
    assert agents[0]._client.provider_name == "openai"
    assert agents[0]._client.model_name == "gpt-4.1-mini"


def test_build_review_agents_resolves_openai_key_from_env_mapping() -> None:
    settings = Settings(
        review=ReviewSettings(
            security=True,
            performance=False,
            architecture=False,
            bug=False,
            test_coverage=False,
        ),
        model=ModelSettings(provider="openai", model_name="gpt-4.1-mini"),
    )

    agents = build_review_agents(settings, env={"OPENAI_API_KEY": "sk-test"})

    assert [agent.name for agent in agents] == ["security"]
    assert agents[0]._client.provider_name == "openai"


def test_build_review_agents_wires_openai_compatible_provider() -> None:
    settings = Settings(
        review=ReviewSettings(
            security=True,
            performance=False,
            architecture=False,
            bug=False,
            test_coverage=False,
        ),
        model=ModelSettings(
            provider="openai-compatible",
            model_name="openai/gpt-oss-20b",
            base_url="http://localhost:8000/v1",
            api_key_env="OPENAI_COMPATIBLE_API_KEY",
        ),
    )

    agents = build_review_agents(settings, env={"OPENAI_COMPATIBLE_API_KEY": "local-key"})

    assert [agent.name for agent in agents] == ["security"]
    assert agents[0]._client.provider_name == "openai-compatible"
    assert agents[0]._client.model_name == "openai/gpt-oss-20b"


def test_build_review_agents_rejects_unimplemented_provider() -> None:
    settings = Settings(model=ModelSettings(provider="transformers"))

    with pytest.raises(UnsupportedModelProviderError, match="transformers"):
        build_review_agents(settings)
