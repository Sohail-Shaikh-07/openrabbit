"""Tests for ``github_.state``."""

from __future__ import annotations

import json
from pathlib import Path

from github_ import (
    FileStateStore,
    InMemoryStateStore,
    PollState,
    SeenPullRequest,
)


def _sample() -> PollState:
    return PollState(
        pull_requests={
            1: SeenPullRequest(number=1, updated_at="2026-01-01T00:00:00", head_sha="a" * 40),
            2: SeenPullRequest(number=2, updated_at="2026-01-02T00:00:00", head_sha="b" * 40),
        }
    )


def test_empty_returns_no_pull_requests() -> None:
    assert PollState.empty().pull_requests == {}


def test_with_pull_request_is_immutable_update() -> None:
    state = PollState.empty()
    new_pr = SeenPullRequest(number=5, updated_at="2026-01-01T00:00:00", head_sha="z" * 40)

    next_state = state.with_pull_request(new_pr)

    assert state.pull_requests == {}
    assert next_state.pull_requests == {5: new_pr}


def test_in_memory_store_round_trip() -> None:
    store = InMemoryStateStore()
    assert store.load() == PollState.empty()

    sample = _sample()
    store.save(sample)
    assert store.load() == sample


def test_file_store_load_missing_returns_empty(tmp_path: Path) -> None:
    store = FileStateStore(tmp_path / "state.json")
    assert store.load() == PollState.empty()


def test_file_store_save_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "state.json"
    store = FileStateStore(target)

    store.save(_sample())

    assert target.is_file()
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["version"] == FileStateStore.SCHEMA_VERSION
    assert len(raw["pull_requests"]) == 2


def test_file_store_round_trip(tmp_path: Path) -> None:
    store = FileStateStore(tmp_path / "state.json")
    sample = _sample()

    store.save(sample)
    loaded = store.load()

    assert loaded == sample


def test_file_store_unknown_version_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 999, "pull_requests": []}), encoding="utf-8")

    assert FileStateStore(path).load() == PollState.empty()


def test_file_store_save_is_atomic_via_rename(tmp_path: Path) -> None:
    """The .tmp sidecar must not be left behind after a successful save."""
    path = tmp_path / "state.json"
    FileStateStore(path).save(_sample())

    siblings = list(tmp_path.iterdir())
    assert siblings == [path]
