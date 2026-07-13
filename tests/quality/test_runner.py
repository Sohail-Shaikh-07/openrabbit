"""Tests for deterministic local quality-gate execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from configs.schema import QualitySettings
from quality.models import ToolStatus
from quality.runner import CommandExecution, LocalQualityRunner


def test_runner_parses_ruff_json_into_structured_diagnostics(tmp_path: Path) -> None:
    output = json.dumps(
        [
            {
                "filename": "src/app.py",
                "location": {"row": 12, "column": 5},
                "code": "F821",
                "message": "Undefined name `value`",
            }
        ]
    )

    def execute(
        command: tuple[str, ...], workspace: Path, timeout_seconds: int
    ) -> CommandExecution:
        assert command[-3:] == ("check", "--output-format=json", ".")
        assert workspace == tmp_path
        assert timeout_seconds == 30
        return CommandExecution(exit_code=1, stdout=output, stderr="", duration_ms=14.5)

    runner = LocalQualityRunner(
        QualitySettings(
            enabled=True,
            auto_detect=False,
            tools=["ruff"],
            timeout_seconds=30,
        ),
        executor=execute,
        availability=lambda _tool, _workspace: True,
    )

    results = runner.run(tmp_path)

    assert len(results) == 1
    result = results[0]
    assert result.tool == "ruff"
    assert result.status is ToolStatus.failed
    assert result.exit_code == 1
    assert result.duration_ms == 14.5
    assert result.diagnostics[0].file == "src/app.py"
    assert result.diagnostics[0].line == 12
    assert result.diagnostics[0].column == 5
    assert result.diagnostics[0].code == "F821"
    assert result.diagnostics[0].message == "Undefined name `value`"
    serialized = result.as_dict()
    assert serialized["command"][0] == Path(command := result.command[0]).name
    assert str(Path(command).parent) not in json.dumps(serialized)


def test_runner_reports_timeout_without_raw_process_output(tmp_path: Path) -> None:
    def execute(
        _command: tuple[str, ...], _workspace: Path, _timeout_seconds: int
    ) -> CommandExecution:
        return CommandExecution(
            exit_code=None,
            stdout="possibly sensitive output",
            stderr="",
            duration_ms=1000.0,
            timed_out=True,
        )

    runner = LocalQualityRunner(
        QualitySettings(enabled=True, auto_detect=False, tools=["pytest"], timeout_seconds=1),
        executor=execute,
        availability=lambda _tool, _workspace: True,
    )

    result = runner.run(tmp_path)[0]

    assert result.status is ToolStatus.timed_out
    assert result.summary == "Timed out after 1 seconds"
    assert "sensitive" not in json.dumps(result.as_dict())


def test_runner_marks_explicit_missing_tool_unavailable(tmp_path: Path) -> None:
    called = False

    def execute(
        _command: tuple[str, ...], _workspace: Path, _timeout_seconds: int
    ) -> CommandExecution:
        nonlocal called
        called = True
        raise AssertionError("unavailable tools must not execute")

    runner = LocalQualityRunner(
        QualitySettings(enabled=True, auto_detect=False, tools=["semgrep"]),
        executor=execute,
        availability=lambda _tool, _workspace: False,
    )

    result = runner.run(tmp_path)[0]

    assert called is False
    assert result.status is ToolStatus.unavailable
    assert result.command == ()


def test_runner_auto_detects_only_configured_repository_tools(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest"}, "devDependencies": {"eslint": "9.0.0"}}),
        encoding="utf-8",
    )

    def execute(
        _command: tuple[str, ...], _workspace: Path, _timeout_seconds: int
    ) -> CommandExecution:
        return CommandExecution(exit_code=0, stdout="", stderr="", duration_ms=1.0)

    runner = LocalQualityRunner(
        QualitySettings(enabled=True, auto_detect=True, tools=[]),
        executor=execute,
        availability=lambda _tool, _workspace: True,
    )

    assert [result.tool for result in runner.run(tmp_path)] == [
        "ruff",
        "pytest",
        "eslint",
        "npm-test",
    ]


def test_runner_parses_mypy_text_diagnostics(tmp_path: Path) -> None:
    output = "src/service.py:8:14: error: Incompatible return value type [return-value]\n"

    def execute(
        _command: tuple[str, ...], _workspace: Path, _timeout_seconds: int
    ) -> CommandExecution:
        return CommandExecution(exit_code=1, stdout=output, stderr="", duration_ms=2.0)

    runner = LocalQualityRunner(
        QualitySettings(enabled=True, auto_detect=False, tools=["mypy"]),
        executor=execute,
        availability=lambda _tool, _workspace: True,
    )

    diagnostic = runner.run(tmp_path)[0].diagnostics[0]

    assert diagnostic.file == "src/service.py"
    assert diagnostic.line == 8
    assert diagnostic.column == 14
    assert diagnostic.severity == "error"
    assert diagnostic.code == "return-value"


def test_semgrep_uses_local_cli_and_repository_rules(tmp_path: Path) -> None:
    (tmp_path / ".semgrep.yml").write_text("rules: []\n", encoding="utf-8")

    def execute(
        command: tuple[str, ...], _workspace: Path, _timeout_seconds: int
    ) -> CommandExecution:
        assert Path(command[0]).stem == "semgrep"
        assert command[1:] == ("scan", "--json", "--config", ".semgrep.yml")
        return CommandExecution(exit_code=0, stdout='{"results": []}', stderr="", duration_ms=1)

    runner = LocalQualityRunner(
        QualitySettings(enabled=True, auto_detect=False, tools=["semgrep"]),
        executor=execute,
        availability=lambda _tool, _workspace: True,
    )

    assert runner.run(tmp_path)[0].status is ToolStatus.passed


@pytest.mark.parametrize(
    ("tool", "output", "expected_file", "expected_code"),
    [
        (
            "bandit",
            json.dumps(
                {
                    "results": [
                        {
                            "filename": "src/auth.py",
                            "line_number": 9,
                            "issue_severity": "HIGH",
                            "issue_text": "Unsafe call",
                            "test_id": "B602",
                        }
                    ]
                }
            ),
            "src/auth.py",
            "B602",
        ),
        (
            "semgrep",
            json.dumps(
                {
                    "results": [
                        {
                            "path": "src/query.py",
                            "start": {"line": 11, "col": 3},
                            "check_id": "python.sql-injection",
                            "extra": {"severity": "ERROR", "message": "Unsafe SQL"},
                        }
                    ]
                }
            ),
            "src/query.py",
            "python.sql-injection",
        ),
        (
            "eslint",
            json.dumps(
                [
                    {
                        "filePath": "src/app.js",
                        "messages": [
                            {
                                "line": 4,
                                "column": 2,
                                "severity": 2,
                                "message": "Unexpected variable",
                                "ruleId": "no-undef",
                            }
                        ],
                    }
                ]
            ),
            "src/app.js",
            "no-undef",
        ),
    ],
)
def test_runner_normalizes_json_analyzer_diagnostics(
    tmp_path: Path,
    tool: str,
    output: str,
    expected_file: str,
    expected_code: str,
) -> None:
    def execute(
        _command: tuple[str, ...], _workspace: Path, _timeout_seconds: int
    ) -> CommandExecution:
        return CommandExecution(exit_code=1, stdout=output, stderr="", duration_ms=1)

    runner = LocalQualityRunner(
        QualitySettings(enabled=True, auto_detect=False, tools=[tool]),
        executor=execute,
        availability=lambda _tool, _workspace: True,
    )

    diagnostic = runner.run(tmp_path)[0].diagnostics[0]

    assert diagnostic.file == expected_file
    assert diagnostic.code == expected_code
