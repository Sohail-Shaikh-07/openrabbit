"""Tests for CodeRabbit-style review controls."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import review_controls
from agents.prompting import format_prompt_diff
from configs.schema import AstInstruction, PathInstruction, ReviewSettings
from github_.diff import DiffLine, Hunk
from github_.models import PullRequestFile
from github_.pr import ParsedFile
from review_controls import apply_review_controls, prepare_review_controls
from review_controls.ast import match_ast_instructions


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


def _parsed_file(
    path: str,
    *,
    additions: int = 1,
    patch: bool = True,
    source_text: str | None = None,
) -> ParsedFile:
    hunk = Hunk(
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=max(1, additions),
        lines=[
            DiffLine(kind="addition", text=f"value_{index} = {index}") for index in range(additions)
        ],
    )
    return ParsedFile(
        file=PullRequestFile(
            sha="file-sha",
            filename=path,
            status="modified",
            additions=additions,
            deletions=0,
            changes=additions,
            patch="@@ -1 +1 @@" if patch else None,
        ),
        hunks=[hunk] if patch else [],
        source_text=source_text,
    )


def _payload_with_sha(files: list[object], sha: str = "abc123") -> object:
    return SimpleNamespace(files=files, head_sha=sha)


def _ast_rule(**overrides: object) -> AstInstruction:
    values: dict[str, object] = {
        "path": "src/**",
        "languages": ["python"],
        "symbols": ["function"],
        "name_pattern": "update_*",
        "instructions": "Require authorization.",
    }
    values.update(overrides)
    return AstInstruction(**values)


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


@pytest.mark.asyncio
async def test_prepare_review_controls_does_not_load_source_without_ast_rules() -> None:
    calls: list[tuple[str, str, int]] = []

    async def load(path: str, ref: str, max_bytes: int) -> str:
        calls.append((path, ref, max_bytes))
        return "def update_task():\n    return changed\n"

    result = await prepare_review_controls(
        _payload_with_sha([_parsed_file("src/api/tasks.py")]),
        ReviewSettings(),
        source_loader=load,
    )

    assert calls == []
    assert result.ast_matches == []
    assert result.warnings == []


@pytest.mark.asyncio
async def test_prepare_review_controls_skips_ineligible_ast_sources() -> None:
    calls: list[tuple[str, str, int]] = []

    async def load(path: str, ref: str, max_bytes: int) -> str:
        calls.append((path, ref, max_bytes))
        return "def update_task():\n    return changed\n"

    settings = ReviewSettings(
        path_exclude=["src/excluded.py"],
        ast_instructions=[_ast_rule()],
    )
    result = await prepare_review_controls(
        _payload_with_sha(
            [
                _parsed_file("src/excluded.py"),
                _parsed_file("src/generated/client.py"),
                _parsed_file("src/binary.py", patch=False),
                _parsed_file("src/main.go"),
                _parsed_file("docs/notes.py"),
            ]
        ),
        settings,
        source_loader=load,
    )

    assert calls == []
    assert result.unsupported_paths == ["src/main.go"]
    assert {item.path: item.reason for item in result.skipped_paths} == {
        "src/excluded.py": "path_excluded",
        "src/generated/client.py": "generated",
    }


@pytest.mark.asyncio
async def test_prepare_review_controls_uses_segment_aware_ast_rule_paths() -> None:
    calls: list[tuple[str, str, int]] = []

    async def load(path: str, ref: str, max_bytes: int) -> str:
        calls.append((path, ref, max_bytes))
        return "def update_task():\n    return changed\n"

    await prepare_review_controls(
        _payload_with_sha([_parsed_file("src/api/tasks.py")]),
        ReviewSettings(ast_instructions=[_ast_rule(path="src/*.py")]),
        source_loader=load,
    )

    assert calls == []


@pytest.mark.asyncio
async def test_prepare_review_controls_loads_and_matches_ast_source() -> None:
    calls: list[tuple[str, str, int]] = []

    async def load(path: str, ref: str, max_bytes: int) -> str:
        calls.append((path, ref, max_bytes))
        return "def update_task():\n    return changed\n"

    settings = ReviewSettings(
        path_exclude=["src/legacy/**"],
        path_instructions=[
            PathInstruction(path="src/api/**", instructions="Check API authorization.")
        ],
        ast_instructions=[_ast_rule(path="src/api/**")],
    )
    result = await prepare_review_controls(
        _payload_with_sha(
            [
                _parsed_file("src/api/tasks.py"),
                _parsed_file("src/legacy/old.py"),
            ]
        ),
        settings,
        source_loader=load,
    )

    assert calls == [("src/api/tasks.py", "abc123", 524288)]
    assert result.ast_matches[0].symbol.name == "update_task"
    assert result.warnings == []
    assert [(item.path, item.reason) for item in result.skipped_paths] == [
        ("src/legacy/old.py", "path_excluded")
    ]
    assert result.filtered_payload.openrabbit_path_instructions == settings.path_instructions
    assert result.filtered_payload.openrabbit_skipped_paths == [
        {"path": "src/legacy/old.py", "reason": "path_excluded"}
    ]


@pytest.mark.asyncio
async def test_prepare_review_controls_limits_ast_source_concurrency() -> None:
    active = 0
    max_active = 0

    async def load(path: str, ref: str, max_bytes: int) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "def update_task():\n    return changed\n"

    files = [_parsed_file(f"src/api/tasks_{index}.py") for index in range(8)]
    result = await prepare_review_controls(
        _payload_with_sha(files),
        ReviewSettings(ast_instructions=[_ast_rule()]),
        source_loader=load,
    )

    assert max_active == 4
    assert len(result.ast_matches) == 8


@pytest.mark.asyncio
async def test_prepare_review_controls_fails_open_with_sanitized_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="review_controls")

    async def load(path: str, ref: str, max_bytes: int) -> str:
        if path.endswith("broken.py"):
            raise RuntimeError("loader-secret-token")
        return "def update_task():\n    return source-body-secret\n"

    result = await prepare_review_controls(
        _payload_with_sha(
            [
                _parsed_file("src/api/broken.py"),
                _parsed_file("src/api/healthy.py"),
            ]
        ),
        ReviewSettings(ast_instructions=[_ast_rule()]),
        source_loader=load,
    )

    assert [(item.path, item.reason) for item in result.warnings] == [
        ("src/api/broken.py", "RuntimeError")
    ]
    assert [item.path for item in result.ast_matches] == ["src/api/healthy.py"]
    broken, healthy = result.filtered_payload.files
    assert broken.source_text is None
    assert broken.source_warning == "RuntimeError"
    assert healthy.source_warning is None
    assert "loader-secret-token" not in caplog.text
    assert "source-body-secret" not in caplog.text


def test_apply_review_controls_sanitizes_parser_errors_and_keeps_matching_files(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="review_controls")
    real_match = match_ast_instructions

    def fail_one_parser(file_: object, rules: list[AstInstruction]) -> object:
        if str(getattr(file_, "path", "")).endswith("broken.py"):
            raise ValueError("parser-secret-token")
        return real_match(file_, rules)

    monkeypatch.setattr(review_controls, "match_ast_instructions", fail_one_parser)
    result = apply_review_controls(
        _payload_with_sha(
            [
                _parsed_file(
                    "src/api/broken.py",
                    source_text="def update_task():\n    return parser-source-secret\n",
                ),
                _parsed_file(
                    "src/api/healthy.py",
                    source_text="def update_task():\n    return changed\n",
                ),
            ]
        ),
        ReviewSettings(ast_instructions=[_ast_rule()]),
    )

    assert [item.path for item in result.ast_matches] == ["src/api/healthy.py"]
    assert [(item.path, item.reason) for item in result.warnings] == [
        ("src/api/broken.py", "parser_ValueError")
    ]
    assert "parser-secret-token" not in caplog.text
    assert "parser-source-secret" not in caplog.text


def test_apply_review_controls_preserves_deterministic_ast_metadata() -> None:
    rules = [
        _ast_rule(instructions="First rule."),
        _ast_rule(instructions="Second rule."),
    ]
    result = apply_review_controls(
        _payload_with_sha(
            [
                _parsed_file(
                    "src/api/first.py",
                    source_text="def update_first():\n    return changed\n",
                ),
                _parsed_file(
                    "src/api/second.py",
                    source_text="def update_second():\n    return changed\n",
                ),
            ]
        ),
        ReviewSettings(ast_instructions=rules),
    )

    assert [(item.path, item.rule_index) for item in result.ast_matches] == [
        ("src/api/first.py", 0),
        ("src/api/first.py", 1),
        ("src/api/second.py", 0),
        ("src/api/second.py", 1),
    ]
    assert result.filtered_payload.openrabbit_controls_applied is True
    assert result.filtered_payload.openrabbit_ast_instructions == result.ast_matches
    assert result.filtered_payload.openrabbit_control_warnings == []
