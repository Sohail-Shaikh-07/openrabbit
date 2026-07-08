"""Shared CLI output format helpers."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TextIO


class OutputFormat(StrEnum):
    """Structured output formats supported by read-only PR commands."""

    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"


def render_json(data: object, out: TextIO) -> None:
    """Write deterministic JSON for script consumers."""
    json.dump(data, out, indent=2, sort_keys=True)
    print("", file=out)
