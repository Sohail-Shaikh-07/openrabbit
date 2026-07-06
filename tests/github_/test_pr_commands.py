"""Tests for OpenRabbit PR comment commands."""

from __future__ import annotations

import json
from pathlib import Path

from github_.pr_commands import (
    CommandState,
    FileCommandStateStore,
    InMemoryCommandStateStore,
    parse_openrabbit_command,
)


def test_parse_openrabbit_commands() -> None:
    assert parse_openrabbit_command("@openrabbit review").kind == "review"  # type: ignore[union-attr]
    assert parse_openrabbit_command("@openrabbit full review").kind == "full_review"  # type: ignore[union-attr]
    assert parse_openrabbit_command("@openrabbit improve").kind == "improve"  # type: ignore[union-attr]
    assert parse_openrabbit_command("@openrabbit pause").kind == "pause"  # type: ignore[union-attr]
    assert parse_openrabbit_command("@openrabbit resume").kind == "resume"  # type: ignore[union-attr]

    ask = parse_openrabbit_command("@openrabbit ask what changed here?")

    assert ask is not None
    assert ask.kind == "ask"
    assert ask.question == "what changed here?"


def test_parse_ignores_non_commands_and_empty_ask() -> None:
    assert parse_openrabbit_command("please review this") is None
    assert parse_openrabbit_command("@otherbot review") is None
    assert parse_openrabbit_command("@openrabbit ask") is None


def test_in_memory_command_state_tracks_pause_and_comment_cursor() -> None:
    store = InMemoryCommandStateStore()
    state = CommandState.empty()

    store.save(state.pause(7).mark_comment_seen(7, 123))

    loaded = store.load()
    assert loaded.is_paused(7)
    assert loaded.last_seen_comment_id(7) == 123
    assert not loaded.resume(7).is_paused(7)


def test_file_command_state_store_round_trips(tmp_path: Path) -> None:
    path = tmp_path / ".openrabbit" / "commands.json"
    store = FileCommandStateStore(path)

    store.save(CommandState.empty().pause(3).mark_comment_seen(3, 456))

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == FileCommandStateStore.SCHEMA_VERSION
    loaded = store.load()
    assert loaded.is_paused(3)
    assert loaded.last_seen_comment_id(3) == 456
