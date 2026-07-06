"""Tests for CodeRabbit-style review controls."""

from __future__ import annotations

from types import SimpleNamespace

from agents.prompting import format_prompt_diff
from configs.schema import PathInstruction, ReviewSettings
from github_.diff import DiffLine, Hunk
from review_controls import apply_review_controls


def _payload(files: list[object]) -> object:
    return SimpleNamespace(files=files)


def _file(
    path: str,
    *,
    additions: int = 1,
    deletions: int = 0,
    patch: bool = True,
) -> object:
    hunk = Hunk(
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=max(1, additions),
        lines=[
            DiffLine(kind="addition", text=f"value_{index} = {index}") for index in range(additions)
        ],
    )
    return SimpleNamespace(
        path=path,
        status="modified",
        is_binary=not patch,
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
        hunks=[hunk] if patch else [],
    )


def test_review_controls_apply_include_exclude_and_generated_defaults() -> None:
    settings = ReviewSettings(
        path_include=["src/**"],
        path_exclude=["src/legacy/**"],
    )
    result = apply_review_controls(
        _payload(
            [
                _file("src/app.py"),
                _file("src/legacy/old.py"),
                _file("docs/usage.md"),
                _file("src/generated/client.py"),
            ]
        ),
        settings,
    )

    assert [file_.path for file_ in result.filtered_payload.files] == ["src/app.py"]
    assert {skipped.path: skipped.reason for skipped in result.skipped_paths} == {
        "src/legacy/old.py": "path_excluded",
        "docs/usage.md": "path_not_included",
        "src/generated/client.py": "generated",
    }


def test_review_controls_can_include_generated_files() -> None:
    settings = ReviewSettings(include_generated=True)
    result = apply_review_controls(_payload([_file("src/generated/client.py")]), settings)

    assert [file_.path for file_ in result.filtered_payload.files] == ["src/generated/client.py"]
    assert result.skipped_paths == []


def test_review_controls_enforce_max_files_and_changed_lines() -> None:
    settings = ReviewSettings(max_files=1, max_changed_lines=5)
    result = apply_review_controls(
        _payload(
            [
                _file("src/large.py", additions=6),
                _file("src/one.py", additions=2),
                _file("src/two.py", additions=2),
            ]
        ),
        settings,
    )

    assert [file_.path for file_ in result.filtered_payload.files] == ["src/one.py"]
    assert {skipped.path: skipped.reason for skipped in result.skipped_paths} == {
        "src/large.py": "max_changed_lines",
        "src/two.py": "max_files",
    }


def test_review_controls_add_profile_and_path_instructions_to_prompt_diff() -> None:
    settings = ReviewSettings(
        profile="chill",
        path_instructions=[
            PathInstruction(
                path="src/api/**",
                instructions="Require explicit authorization before mutations.",
            )
        ],
    )
    result = apply_review_controls(_payload([_file("src/api/tasks.py")]), settings)

    diff = format_prompt_diff(result.filtered_payload)

    assert "Review controls:" in diff
    assert "Profile: chill" in diff
    assert "src/api/**: Require explicit authorization before mutations." in diff
