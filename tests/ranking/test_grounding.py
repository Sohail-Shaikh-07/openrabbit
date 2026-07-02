"""Tests for grounding model findings in the actual PR diff."""

from __future__ import annotations

from types import SimpleNamespace

from agents.models import Finding, Severity
from github_.diff import DiffLine, Hunk
from ranking.grounding import filter_grounded_findings


def _finding(file: str, line: int, title: str = "Issue") -> Finding:
    return Finding(
        severity=Severity.medium,
        category="bug",
        file=file,
        line=line,
        confidence=0.9,
        title=title,
        reason="reason",
        suggestion="suggestion",
        fix="",
    )


def _payload() -> object:
    return SimpleNamespace(
        files=[
            SimpleNamespace(
                path="src/search.py",
                hunks=[
                    Hunk(
                        old_start=8,
                        old_lines=2,
                        new_start=10,
                        new_lines=4,
                        lines=[
                            DiffLine(kind="context", text="def search_tasks(q):"),
                            DiffLine(
                                kind="addition", text="    raw_sql = f'SELECT * FROM tasks {q}'"
                            ),
                            DiffLine(kind="addition", text="    return db.execute(raw_sql)"),
                        ],
                    )
                ],
            )
        ]
    )


def test_filter_keeps_finding_on_changed_added_line() -> None:
    result = filter_grounded_findings([_finding("src/search.py", 11)], _payload())

    assert len(result.kept) == 1
    assert result.dropped == []


def test_filter_drops_finding_on_file_not_changed_by_pr() -> None:
    result = filter_grounded_findings([_finding("tests/test_fake.py", 10)], _payload())

    assert result.kept == []
    assert result.dropped[0].reason == "file_not_changed"


def test_filter_drops_line_level_finding_outside_changed_lines() -> None:
    result = filter_grounded_findings([_finding("src/search.py", 20)], _payload())

    assert result.kept == []
    assert result.dropped[0].reason == "line_not_changed"


def test_filter_keeps_file_level_finding_for_changed_file() -> None:
    result = filter_grounded_findings([_finding("src/search.py", 0)], _payload())

    assert len(result.kept) == 1
    assert result.dropped == []
