"""Tests for memory backend contracts."""

from __future__ import annotations

from pathlib import Path

from memory.backends import PullRequestMemoryBackend
from memory.store import SQLitePullRequestMemory


def test_sqlite_memory_implements_backend_contract(tmp_path: Path) -> None:
    backend = SQLitePullRequestMemory(tmp_path / "openrabbit.db")

    assert isinstance(backend, PullRequestMemoryBackend)
