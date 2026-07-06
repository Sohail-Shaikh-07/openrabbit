"""Tests for ``cli.commands.index``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from typer.testing import CliRunner

from cli.commands.index import run_qdrant_health_check_blocking
from cli.main import app
from rag.vector_store import COLLECTION_DOCS, COLLECTION_FUNCTIONS

_RUNNER = CliRunner()


def test_qdrant_health_check_reports_available_collections() -> None:
    store = MagicMock()
    store.list_collections = AsyncMock(return_value={COLLECTION_FUNCTIONS, COLLECTION_DOCS})
    store.close = AsyncMock()

    result = run_qdrant_health_check_blocking(store=store)

    assert result.ok is True
    assert result.collections == [COLLECTION_DOCS, COLLECTION_FUNCTIONS]
    assert result.message == "Qdrant reachable"
    store.close.assert_awaited_once()


def test_qdrant_health_check_reports_connection_failure() -> None:
    store = MagicMock()
    store.list_collections = AsyncMock(side_effect=RuntimeError("qdrant down"))
    store.close = AsyncMock()

    result = run_qdrant_health_check_blocking(store=store)

    assert result.ok is False
    assert "qdrant down" in result.message
    store.close.assert_awaited_once()


def test_index_cli_accepts_health_flag(scaffold_repo) -> None:  # type: ignore[no-untyped-def]
    result = _RUNNER.invoke(
        app,
        [
            "index",
            "--workspace",
            str(scaffold_repo),
            "--health",
        ],
    )

    assert result.exit_code != 2
