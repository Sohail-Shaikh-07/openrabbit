"""Tests for AST-scoped review instructions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from configs.schema import AstInstruction
from github_.diff import DiffLine, Hunk
from review_controls.ast import (
    added_lines,
    extract_ast_symbols,
    language_for_path,
    match_ast_instructions,
)


def _parsed_file(path: str, *, source: str, hunk: Hunk) -> object:
    return SimpleNamespace(path=path, source_text=source, hunks=[hunk])


def _rule(**overrides: object) -> AstInstruction:
    values: dict[str, object] = {
        "path": "**",
        "languages": [],
        "symbols": ["function"],
        "name_pattern": "*",
        "instructions": "Review this symbol.",
    }
    values.update(overrides)
    return AstInstruction(**values)


def test_extract_python_symbols_with_one_based_spans() -> None:
    source = """def top():
    return 1

class Service:
    def update_task(self):
        def nested():
            return 2
        return nested()
"""

    symbols = extract_ast_symbols(source, "python")

    assert [(item.kind.value, item.name, item.start_line, item.end_line) for item in symbols] == [
        ("function", "top", 1, 2),
        ("class", "Service", 4, 8),
        ("method", "update_task", 5, 8),
        ("function", "nested", 6, 7),
    ]


def test_extract_javascript_symbols_including_methods_and_arrow_functions() -> None:
    source = """function named() {
  return 1;
}

const arrow = () => 2;

class Service {
  updateTask() {
    return arrow();
  }
}
"""

    symbols = extract_ast_symbols(source, "javascript")

    assert [(item.kind.value, item.name, item.start_line, item.end_line) for item in symbols] == [
        ("function", "named", 1, 3),
        ("function", "arrow", 5, 5),
        ("class", "Service", 7, 11),
        ("method", "updateTask", 8, 10),
    ]


def test_extract_typescript_symbols() -> None:
    source = """function parseTask(input: string): string {
  return input;
}

class TaskService {
  getTask(id: string): string {
    return id;
  }
}
"""

    symbols = extract_ast_symbols(source, "typescript")

    assert [(item.kind.value, item.name, item.start_line, item.end_line) for item in symbols] == [
        ("function", "parseTask", 1, 3),
        ("class", "TaskService", 5, 9),
        ("method", "getTask", 6, 8),
    ]


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("app.py", "python"),
        ("web/app.jsx", "javascript"),
        ("web/app.tsx", "typescript"),
        ("main.go", None),
    ],
)
def test_language_for_path(path: str, language: str | None) -> None:
    assert language_for_path(path) == language


def test_extract_ast_symbols_returns_no_symbols_for_unsupported_language() -> None:
    assert extract_ast_symbols("func main() {}", "go") == []


def test_added_lines_counts_only_additions() -> None:
    file_ = _parsed_file(
        "src/api/tasks.py",
        source="",
        hunk=Hunk(
            old_start=2,
            old_lines=3,
            new_start=2,
            new_lines=3,
            lines=[
                DiffLine(kind="context", text="before"),
                DiffLine(kind="deletion", text="removed"),
                DiffLine(kind="addition", text="added"),
                DiffLine(kind="context", text="after"),
            ],
        ),
    )

    assert added_lines(file_) == frozenset({3})


def test_match_ast_rule_only_when_added_line_overlaps_symbol() -> None:
    file_ = _parsed_file(
        "src/api/tasks.py",
        source="def update_task():\n    return changed\n",
        hunk=Hunk(
            old_start=1,
            old_lines=2,
            new_start=1,
            new_lines=2,
            lines=[
                DiffLine(kind="context", text="def update_task():"),
                DiffLine(kind="addition", text="    return changed"),
                DiffLine(kind="deletion", text="    return old"),
            ],
        ),
    )
    rule = _rule(
        path="src/api/**",
        languages=["python"],
        symbols=["function"],
        name_pattern="update_*",
        instructions="Require authorization.",
    )

    matches = match_ast_instructions(file_, [rule])

    assert [(item.path, item.symbol.name, item.instructions) for item in matches] == [
        ("src/api/tasks.py", "update_task", "Require authorization.")
    ]


def test_match_ast_rules_ignore_deletion_only_hunks() -> None:
    file_ = _parsed_file(
        "src/api/tasks.py",
        source="def update_task():\n    return current\n",
        hunk=Hunk(
            old_start=1,
            old_lines=2,
            new_start=1,
            new_lines=2,
            lines=[
                DiffLine(kind="context", text="def update_task():"),
                DiffLine(kind="deletion", text="    return removed"),
            ],
        ),
    )

    assert match_ast_instructions(file_, [_rule()]) == []


def test_match_ast_rules_require_matching_path_language_kind_and_name() -> None:
    file_ = _parsed_file(
        "src/api/tasks.py",
        source="class Service:\n    def update_task(self):\n        return 1\n",
        hunk=Hunk(
            old_start=1,
            old_lines=3,
            new_start=1,
            new_lines=3,
            lines=[
                DiffLine(kind="context", text="class Service:"),
                DiffLine(kind="context", text="    def update_task(self):"),
                DiffLine(kind="addition", text="        return 1"),
            ],
        ),
    )
    rules = [
        _rule(path="src/web/**", symbols=["method"]),
        _rule(languages=["javascript"], symbols=["method"]),
        _rule(symbols=["function"], name_pattern="update_*"),
        _rule(symbols=["method"], name_pattern="create_*"),
        _rule(symbols=["method"], name_pattern="update_*", instructions="Authorize updates."),
    ]

    matches = match_ast_instructions(file_, rules)

    assert [
        (item.rule_index, item.symbol.kind.value, item.symbol.name, item.instructions)
        for item in matches
    ] == [(4, "method", "update_task", "Authorize updates.")]


def test_match_ast_rules_preserve_configuration_order_and_deduplicate() -> None:
    file_ = _parsed_file(
        "src/api/tasks.py",
        source="def update_task():\n    first = changed\n    return first\n",
        hunk=Hunk(
            old_start=1,
            old_lines=3,
            new_start=1,
            new_lines=3,
            lines=[
                DiffLine(kind="context", text="def update_task():"),
                DiffLine(kind="addition", text="    first = changed"),
                DiffLine(kind="addition", text="    return first"),
            ],
        ),
    )
    rules = [
        _rule(instructions="First instruction."),
        _rule(instructions="Second instruction."),
    ]

    matches = match_ast_instructions(file_, rules)

    assert [(item.rule_index, item.instructions) for item in matches] == [
        (0, "First instruction."),
        (1, "Second instruction."),
    ]
