"""Unified-diff parsing for GitHub pull request file patches.

GitHub's ``files`` endpoint returns a ``patch`` field that is a small unified
diff (just the hunks for that file, no file headers). We turn it into a list
of structured :class:`Hunk` objects so the review agents do not each have to
parse strings.

The parser is intentionally permissive. Binary files have no patch and that
must not raise. Renames may have an empty patch. Hunk headers without
explicit line counts (``@@ -3 +3 @@``) default to one line on each side, which
matches the unified diff convention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

LineKind = Literal["context", "addition", "deletion", "no_newline_marker"]


@dataclass(frozen=True)
class DiffLine:
    """One line in a hunk, with its origin side recorded."""

    kind: LineKind
    text: str


@dataclass(frozen=True)
class Hunk:
    """A single ``@@`` block from a unified diff."""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLine] = field(default_factory=list)


_HUNK_HEADER = re.compile(r"^@@ -(?P<os>\d+)(?:,(?P<ol>\d+))? \+(?P<ns>\d+)(?:,(?P<nl>\d+))? @@")


def parse_patch(patch: str | None) -> list[Hunk]:
    """Parse a unified diff patch into a list of :class:`Hunk`.

    ``None`` and empty strings both return an empty list. Lines that fall
    outside any hunk header are ignored, which keeps the parser tolerant of
    trailing whitespace or sentinel lines GitHub sometimes appends.
    """
    if not patch:
        return []

    hunks: list[Hunk] = []
    current: Hunk | None = None
    current_lines: list[DiffLine] = []

    def _flush() -> None:
        nonlocal current, current_lines
        if current is not None:
            # Replace the frozen hunk with one carrying the collected lines.
            hunks.append(
                Hunk(
                    old_start=current.old_start,
                    old_lines=current.old_lines,
                    new_start=current.new_start,
                    new_lines=current.new_lines,
                    lines=current_lines,
                )
            )
        current = None
        current_lines = []

    for raw in patch.splitlines():
        header = _HUNK_HEADER.match(raw)
        if header:
            _flush()
            current = Hunk(
                old_start=int(header["os"]),
                old_lines=int(header["ol"]) if header["ol"] is not None else 1,
                new_start=int(header["ns"]),
                new_lines=int(header["nl"]) if header["nl"] is not None else 1,
            )
            current_lines = []
            continue

        if current is None:
            # Lines before the first hunk header are file metadata that the
            # GitHub files endpoint does not usually include. Skip them.
            continue

        if not raw:
            current_lines.append(DiffLine(kind="context", text=""))
            continue

        marker, body = raw[0], raw[1:]
        if marker == "+":
            current_lines.append(DiffLine(kind="addition", text=body))
        elif marker == "-":
            current_lines.append(DiffLine(kind="deletion", text=body))
        elif marker == " ":
            current_lines.append(DiffLine(kind="context", text=body))
        elif marker == "\\":
            # "\ No newline at end of file" - record it but do not let it
            # confuse downstream line counting.
            current_lines.append(DiffLine(kind="no_newline_marker", text=body.lstrip()))
        else:
            # Unknown leading character. Treat as context to avoid losing data.
            current_lines.append(DiffLine(kind="context", text=raw))

    _flush()
    return hunks
