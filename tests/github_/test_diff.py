"""Tests for ``github_.diff.parse_patch``."""

from __future__ import annotations

import pytest

from github_ import parse_patch


def test_none_patch_returns_empty_list() -> None:
    assert parse_patch(None) == []


def test_empty_patch_returns_empty_list() -> None:
    assert parse_patch("") == []


def test_single_hunk_with_addition_and_deletion() -> None:
    patch = "@@ -1,3 +1,3 @@\n" " line one\n" "-line two old\n" "+line two new\n" " line three\n"
    hunks = parse_patch(patch)

    assert len(hunks) == 1
    h = hunks[0]
    assert (h.old_start, h.old_lines, h.new_start, h.new_lines) == (1, 3, 1, 3)
    kinds = [line.kind for line in h.lines]
    assert kinds == ["context", "deletion", "addition", "context"]
    assert h.lines[1].text == "line two old"
    assert h.lines[2].text == "line two new"


def test_multiple_hunks() -> None:
    patch = "@@ -1,2 +1,2 @@\n" "-a\n" "+b\n" "@@ -10,2 +10,2 @@\n" "-c\n" "+d\n"
    hunks = parse_patch(patch)

    assert len(hunks) == 2
    assert hunks[0].new_start == 1
    assert hunks[1].new_start == 10


def test_hunk_header_without_explicit_counts_defaults_to_one() -> None:
    """``@@ -3 +3 @@`` is valid unified-diff shorthand for one line on each side."""
    patch = "@@ -3 +3 @@\n-old\n+new\n"
    hunks = parse_patch(patch)

    assert hunks[0].old_lines == 1
    assert hunks[0].new_lines == 1


def test_no_newline_at_end_of_file_marker_recorded() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new\n\\ No newline at end of file\n"
    hunks = parse_patch(patch)

    kinds = [line.kind for line in hunks[0].lines]
    assert "no_newline_marker" in kinds


def test_lines_before_first_hunk_are_ignored() -> None:
    patch = (
        "diff --git a/file.py b/file.py\n"
        "index abc..def 100644\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    hunks = parse_patch(patch)
    assert len(hunks) == 1
    assert [line.kind for line in hunks[0].lines] == ["deletion", "addition"]


@pytest.mark.parametrize("body", ["", " "])
def test_blank_lines_inside_hunk_treated_as_context(body: str) -> None:
    patch = "@@ -1,3 +1,3 @@\n" f"{body}\n" "-old\n+new\n"
    hunks = parse_patch(patch)
    # First line in the hunk is blank or single-space, classified as context.
    assert hunks[0].lines[0].kind == "context"
