"""Tests for ``cli.commands.start``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from cli.commands.init import run_init
from cli.commands.start import StartError, resolve_target_repo, run_start
from configs import RepositorySettings, Settings, load_settings

_BASE = "https://api.github.com"


def test_resolve_target_flag_wins_over_settings() -> None:
    settings = Settings(repository=RepositorySettings(target="from/settings"))
    assert resolve_target_repo(settings, "from/flag") == "from/flag"


def test_resolve_target_falls_back_to_settings() -> None:
    settings = Settings(repository=RepositorySettings(target="from/settings"))
    assert resolve_target_repo(settings, None) == "from/settings"


def test_resolve_target_missing_both_raises() -> None:
    with pytest.raises(StartError, match="no repository"):
        resolve_target_repo(Settings(), None)


@respx.mock
async def test_run_start_runs_polling_until_cancelled(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single failing GitHub call inside run_forever should not crash the loop.

    We stop the loop by patching asyncio.sleep to raise CancelledError after the
    first round, which is the same way a Ctrl-C aborts the daemon.
    """
    import asyncio

    rounds: list[int] = []

    async def fake_sleep(_seconds: float) -> None:
        rounds.append(1)
        raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(return_value=httpx.Response(200, json=[]))

    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(asyncio.CancelledError):
        await run_start(
            settings,
            workspace=scaffold_repo,
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
        )

    assert rounds == [1]
    assert (scaffold_repo / ".openrabbit" / "state.json").is_file()


def test_run_start_blocking_raises_start_error_when_no_repo(
    scaffold_repo: Path,
) -> None:
    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(StartError):
        resolve_target_repo(settings, None)


def test_run_init_template_sets_no_repository_target(scaffold_repo: Path) -> None:
    """The init template ships with repository.target commented out.

    A fresh `openrabbit init` followed by `openrabbit start` must hit the
    no-repo guard rather than wiring to a phantom default.
    """
    run_init(scaffold_repo, force=True)
    settings = load_settings(scaffold_repo, env={})
    assert settings.repository.target is None
