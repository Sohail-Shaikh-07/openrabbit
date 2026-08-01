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
    assert parse_openrabbit_command("/openrabbit review").kind == "review"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit full review").kind == "full_review"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit improve").kind == "improve"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit pause").kind == "pause"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit resume").kind == "resume"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit ignore").kind == "ignore"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit summary").kind == "summary"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit configuration").kind == "configuration"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit config").kind == "configuration"  # type: ignore[union-attr]
    assert parse_openrabbit_command("/openrabbit learn Use repositories for SQL").kind == "learn"  # type: ignore[union-attr]

    ask = parse_openrabbit_command("/openrabbit ask what changed here?")
    learn = parse_openrabbit_command("/openrabbit learn Prefer bind parameters.")

    assert ask is not None
    assert ask.kind == "ask"
    assert ask.question == "what changed here?"
    assert learn is not None
    assert learn.kind == "learn"
    assert learn.instruction == "Prefer bind parameters."


def test_parse_legacy_mention_commands() -> None:
    assert parse_openrabbit_command("@openrabbit review").kind == "review"  # type: ignore[union-attr]
    learn = parse_openrabbit_command("@openrabbit learn Prefer bind parameters.")

    assert learn is not None
    assert learn.kind == "learn"
    assert learn.instruction == "Prefer bind parameters."


def test_parse_ignores_non_commands_and_empty_ask() -> None:
    assert parse_openrabbit_command("please review this") is None
    assert parse_openrabbit_command("@otherbot review") is None
    assert parse_openrabbit_command("/openrabbit ask") is None
    assert parse_openrabbit_command("/openrabbit learn") is None


def test_in_memory_command_state_tracks_pause_ignore_and_comment_cursor() -> None:
    store = InMemoryCommandStateStore()
    state = CommandState.empty()

    store.save(state.pause(7).ignore(7).mark_comment_seen(7, 123))

    loaded = store.load()
    assert loaded.is_paused(7)
    assert loaded.is_ignored(7)
    assert loaded.last_seen_comment_id(7) == 123
    resumed = loaded.resume(7)
    assert not resumed.is_paused(7)
    assert not resumed.is_ignored(7)


def test_command_state_tracks_out_of_order_completion_and_failures() -> None:
    state = CommandState.empty().record_comment_failure(7, 124)
    state = state.mark_comment_completed(7, 125)

    assert state.failure_attempts(7, 124) == 1
    assert state.is_comment_completed(7, 125)
    assert state.last_seen_comment_id(7) == 0

    state = state.mark_comment_completed(7, 124)
    state = state.advance_comment_cursor(7, [124, 125])

    assert state.last_seen_comment_id(7) == 125
    assert state.failure_attempts(7, 124) == 0
    assert state.completed_comment_ids == {}


def test_file_command_state_store_round_trips(tmp_path: Path) -> None:
    path = tmp_path / ".openrabbit" / "commands.json"
    store = FileCommandStateStore(path)

    state = CommandState.empty().pause(3).ignore(3).mark_comment_seen(3, 456)
    state = state.record_comment_failure(3, 457).mark_comment_completed(3, 458)
    store.save(state)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == FileCommandStateStore.SCHEMA_VERSION
    assert raw["ignored_prs"] == [3]
    loaded = store.load()
    assert loaded.is_paused(3)
    assert loaded.is_ignored(3)
    assert loaded.last_seen_comment_id(3) == 456
    assert loaded.failure_attempts(3, 457) == 1
    assert loaded.is_comment_completed(3, 458)


def test_file_command_state_store_migrates_version_one_state(tmp_path: Path) -> None:
    path = tmp_path / ".openrabbit" / "commands.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "paused_prs": [3],
                "ignored_prs": [],
                "last_seen_comment_ids": {"3": 456},
            }
        ),
        encoding="utf-8",
    )

    loaded = FileCommandStateStore(path).load()

    assert loaded.is_paused(3)
    assert loaded.last_seen_comment_id(3) == 456
    assert loaded.completed_comment_ids == {}
    assert loaded.comment_failure_attempts == {}
