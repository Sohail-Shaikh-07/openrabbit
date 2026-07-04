"""Tests for shared review prompt helpers."""

from __future__ import annotations

from types import SimpleNamespace

from agents.prompting import (
    estimate_prompt_tokens,
    format_changed_line_evidence,
    format_prompt_diff,
)
from github_.diff import DiffLine, Hunk


def _payload(files: list[object]) -> object:
    return SimpleNamespace(files=files)


def _file(
    path: str,
    hunks: list[Hunk],
    status: str = "modified",
    *,
    is_binary: bool = False,
    additions: int = 0,
    deletions: int = 0,
) -> object:
    return SimpleNamespace(
        path=path,
        hunks=hunks,
        status=status,
        is_binary=is_binary,
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
    )


def test_format_changed_line_evidence_lists_added_lines_with_new_line_numbers() -> None:
    hunk = Hunk(
        old_start=64,
        old_lines=5,
        new_start=67,
        new_lines=6,
        lines=[
            DiffLine(kind="context", text="def advanced_search(self, query: str) -> list[Task]:"),
            DiffLine(kind="deletion", text="return []"),
            DiffLine(kind="addition", text="where_clause = f\"title LIKE '%{query}%'\""),
            DiffLine(
                kind="addition", text='rows_sql = text(f"SELECT * FROM tasks WHERE {where_clause}")'
            ),
        ],
    )

    evidence = format_changed_line_evidence(
        _payload([_file("app/repositories/task_repository.py", [hunk])])
    )

    assert "Changed-line evidence:" in evidence
    assert "app/repositories/task_repository.py (modified)" in evidence
    assert "+68 where_clause = f\"title LIKE '%{query}%'\"" in evidence
    assert '+69 rows_sql = text(f"SELECT * FROM tasks WHERE {where_clause}")' in evidence
    assert "return []" not in evidence


def test_format_changed_line_evidence_is_bounded_per_file_and_file_count() -> None:
    hunk = Hunk(
        old_start=1,
        old_lines=1,
        new_start=10,
        new_lines=4,
        lines=[
            DiffLine(kind="addition", text="first = 1"),
            DiffLine(kind="addition", text="second = 2"),
            DiffLine(kind="addition", text="third = 3"),
        ],
    )

    evidence = format_changed_line_evidence(
        _payload(
            [
                _file("app/one.py", [hunk]),
                _file("app/two.py", [hunk]),
            ]
        ),
        max_files=1,
        max_lines_per_file=2,
    )

    assert "app/one.py" in evidence
    assert "app/two.py" not in evidence
    assert "+10 first = 1" in evidence
    assert "+11 second = 2" in evidence
    assert "third = 3" not in evidence
    assert "... 1 additional changed file omitted." in evidence
    assert "... 1 additional added line omitted." in evidence


def test_format_changed_line_evidence_has_total_token_budget() -> None:
    hunk = Hunk(
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=20,
        lines=[
            DiffLine(kind="addition", text=f"value_{index} = '{'x' * 20}'") for index in range(20)
        ],
    )

    evidence = format_changed_line_evidence(
        _payload([_file("app/search.py", [hunk])]),
        max_files=1,
        max_lines_per_file=20,
        max_tokens=35,
    )

    assert estimate_prompt_tokens(evidence) <= 35
    assert "additional changed-line evidence omitted" in evidence


def test_format_prompt_diff_preserves_small_raw_diff() -> None:
    raw_diff = "diff --git a/app.py b/app.py\n+value = 1"

    diff = format_prompt_diff(SimpleNamespace(diff=raw_diff, files=[]))

    assert diff == raw_diff


def test_format_prompt_diff_rebuilds_diff_from_parsed_hunks() -> None:
    hunk = Hunk(
        old_start=10,
        old_lines=2,
        new_start=10,
        new_lines=3,
        lines=[
            DiffLine(kind="context", text="def search_tasks(query):"),
            DiffLine(kind="deletion", text="return []"),
            DiffLine(kind="addition", text="return repository.search(query)"),
        ],
    )

    diff = format_prompt_diff(
        _payload([_file("app/services/search.py", [hunk], additions=1, deletions=1)])
    )

    assert "Compressed diff:" in diff
    assert "diff --git a/app/services/search.py b/app/services/search.py" in diff
    assert "# status: modified; additions: 1; deletions: 1; changes: 2" in diff
    assert "@@ -10,2 +10,3 @@" in diff
    assert " def search_tasks(query):" in diff
    assert "-return []" in diff
    assert "+return repository.search(query)" in diff


def test_format_prompt_diff_prioritizes_risky_files_inside_budget() -> None:
    large_hunk = Hunk(
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=40,
        lines=[
            DiffLine(kind="addition", text=f"value_{index} = '{'x' * 30}'") for index in range(40)
        ],
    )
    files = [
        _file("docs/usage.md", [large_hunk], additions=40),
        _file("app/auth/session.py", [large_hunk], additions=40),
    ]

    diff = format_prompt_diff(_payload(files), max_tokens=120)

    assert estimate_prompt_tokens(diff) <= 120
    assert "app/auth/session.py" in diff
    assert "omitted" in diff
    assert (
        diff.find("app/auth/session.py") < diff.find("docs/usage.md") or "docs/usage.md" not in diff
    )


def test_format_prompt_diff_summarizes_binary_files() -> None:
    diff = format_prompt_diff(
        _payload([_file("assets/logo.png", [], is_binary=True, additions=0, deletions=0)])
    )

    assert "assets/logo.png" in diff
    assert "binary, renamed-without-patch, or too-large patch omitted by GitHub" in diff
