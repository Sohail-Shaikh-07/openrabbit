"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.commands.init import run_init


@pytest.fixture
def scaffold_repo(tmp_path: Path) -> Iterator[Path]:
    """A tmp directory that already looks like a repo OpenRabbit was initialized in.

    Yields the repo root with ``.codereviewer/`` populated from the project
    templates. Used by every test that needs a valid config to load.
    """
    run_init(tmp_path)
    yield tmp_path
