"""Bounded subprocess runner for supported repository quality tools."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from configs.schema import QualitySettings
from quality.models import ToolDiagnostic, ToolRunResult, ToolStatus

Executor = Callable[[tuple[str, ...], Path, int], "CommandExecution"]
AvailabilityCheck = Callable[[str, Path], bool]

_TOOL_ORDER = ("ruff", "mypy", "pytest", "bandit", "semgrep", "eslint", "npm-test")
_MYPY_LINE = re.compile(
    r"^(?P<file>.*?):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>.*?)(?:\s+\[(?P<code>[^]]+)\])?$"
)
_PYTHON_TOOLS = {"ruff", "mypy", "pytest", "bandit"}


@dataclass(frozen=True)
class CommandExecution:
    """Raw command outcome retained only until diagnostics are normalized."""

    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    output_truncated: bool = False


class LocalQualityRunner:
    """Run known local tools with no shell interpolation or network service."""

    def __init__(
        self,
        settings: QualitySettings,
        *,
        executor: Executor | None = None,
        availability: AvailabilityCheck | None = None,
    ) -> None:
        self._settings = settings
        self._executor = executor or self._execute
        self._availability = availability or _tool_available

    def run(self, workspace: Path) -> list[ToolRunResult]:
        """Run configured and detected tools from *workspace*."""
        if not self._settings.enabled:
            return []

        workspace = workspace.resolve()
        results: list[ToolRunResult] = []
        explicit = set(self._settings.tools)
        for tool in self._selected_tools(workspace):
            if not self._availability(tool, workspace):
                if tool in explicit:
                    results.append(
                        ToolRunResult(
                            tool=tool,
                            status=ToolStatus.unavailable,
                            command=(),
                            exit_code=None,
                            duration_ms=0.0,
                            summary="Tool is not installed locally",
                        )
                    )
                continue

            command = _command_for(tool, workspace)
            try:
                execution = self._executor(
                    command,
                    workspace,
                    self._settings.timeout_seconds,
                )
            except OSError as exc:
                results.append(
                    ToolRunResult(
                        tool=tool,
                        status=ToolStatus.error,
                        command=command,
                        exit_code=None,
                        duration_ms=0.0,
                        summary=f"Could not start tool: {type(exc).__name__}",
                    )
                )
                continue

            results.append(self._normalize(tool, command, execution, workspace))
        return results

    def _selected_tools(self, workspace: Path) -> list[str]:
        selected = set(self._settings.tools)
        if self._settings.auto_detect:
            for tool in _TOOL_ORDER:
                if _repository_uses(tool, workspace) and self._availability(tool, workspace):
                    selected.add(tool)
        return [tool for tool in _TOOL_ORDER if tool in selected]

    def _normalize(
        self,
        tool: str,
        command: tuple[str, ...],
        execution: CommandExecution,
        workspace: Path,
    ) -> ToolRunResult:
        if execution.timed_out:
            return ToolRunResult(
                tool=tool,
                status=ToolStatus.timed_out,
                command=command,
                exit_code=None,
                duration_ms=execution.duration_ms,
                summary=f"Timed out after {self._settings.timeout_seconds} seconds",
                output_truncated=execution.output_truncated,
            )

        diagnostics = tuple(
            _parse_diagnostics(tool, execution.stdout, execution.stderr, workspace)[
                : self._settings.max_diagnostics
            ]
        )
        status = ToolStatus.passed if execution.exit_code == 0 else ToolStatus.failed
        if status is ToolStatus.passed:
            summary = "Passed"
        elif diagnostics:
            noun = "diagnostic" if len(diagnostics) == 1 else "diagnostics"
            summary = f"{len(diagnostics)} {noun}"
        else:
            summary = f"Exited with status {execution.exit_code}"
        return ToolRunResult(
            tool=tool,
            status=status,
            command=command,
            exit_code=execution.exit_code,
            duration_ms=execution.duration_ms,
            summary=summary,
            diagnostics=diagnostics,
            output_truncated=execution.output_truncated,
        )

    def _execute(
        self,
        command: tuple[str, ...],
        workspace: Path,
        timeout_seconds: int,
    ) -> CommandExecution:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
            stdout, stdout_truncated = _bounded(completed.stdout, self._settings.max_output_chars)
            stderr, stderr_truncated = _bounded(completed.stderr, self._settings.max_output_chars)
            return CommandExecution(
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=(time.perf_counter() - started) * 1000,
                output_truncated=stdout_truncated or stderr_truncated,
            )
        except subprocess.TimeoutExpired:
            return CommandExecution(
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=(time.perf_counter() - started) * 1000,
                timed_out=True,
            )


def _command_for(tool: str, workspace: Path) -> tuple[str, ...]:
    if tool == "ruff":
        return (sys.executable, "-m", "ruff", "check", "--output-format=json", ".")
    if tool == "mypy":
        return (sys.executable, "-m", "mypy", "--show-error-codes", "--no-color-output")
    if tool == "pytest":
        return (sys.executable, "-m", "pytest", "--no-cov", "-q")
    if tool == "bandit":
        return (sys.executable, "-m", "bandit", "-r", ".", "-f", "json", "-q")
    if tool == "semgrep":
        config = ".semgrep.yml" if (workspace / ".semgrep.yml").is_file() else ".semgrep.yaml"
        return (str(shutil.which("semgrep") or "semgrep"), "scan", "--json", "--config", config)
    if tool == "eslint":
        binary = (
            workspace
            / "node_modules"
            / ".bin"
            / ("eslint.cmd" if sys.platform == "win32" else "eslint")
        )
        executable = str(binary) if binary.is_file() else str(shutil.which("eslint") or "eslint")
        return (executable, ".", "--format", "json")
    if tool == "npm-test":
        return (str(shutil.which("npm") or "npm"), "test", "--if-present")
    raise ValueError(f"unsupported quality tool: {tool}")


def _tool_available(tool: str, workspace: Path) -> bool:
    if tool in _PYTHON_TOOLS:
        return importlib.util.find_spec(tool) is not None
    if tool == "semgrep":
        return shutil.which("semgrep") is not None
    if tool == "eslint":
        suffix = "eslint.cmd" if sys.platform == "win32" else "eslint"
        return (workspace / "node_modules" / ".bin" / suffix).is_file() or shutil.which(
            "eslint"
        ) is not None
    if tool == "npm-test":
        return shutil.which("npm") is not None
    return False


def _repository_uses(tool: str, workspace: Path) -> bool:
    pyproject = _read_text(workspace / "pyproject.toml")
    package = _read_json(workspace / "package.json")
    if tool == "ruff":
        return "[tool.ruff" in pyproject
    if tool == "mypy":
        return "[tool.mypy" in pyproject or (workspace / "mypy.ini").is_file()
    if tool == "pytest":
        return (workspace / "tests").is_dir() or "[tool.pytest" in pyproject
    if tool == "bandit":
        return (workspace / ".bandit").is_file() or "[tool.bandit" in pyproject
    if tool == "semgrep":
        return (workspace / ".semgrep.yml").is_file() or (workspace / ".semgrep.yaml").is_file()
    if tool == "eslint":
        dependencies = _package_dependencies(package)
        return "eslint" in dependencies or any(
            (workspace / name).is_file()
            for name in ("eslint.config.js", "eslint.config.mjs", ".eslintrc", ".eslintrc.json")
        )
    if tool == "npm-test":
        scripts = package.get("scripts")
        return isinstance(scripts, dict) and bool(scripts.get("test"))
    return False


def _parse_diagnostics(
    tool: str,
    stdout: str,
    stderr: str,
    workspace: Path,
) -> list[ToolDiagnostic]:
    text = stdout or stderr
    if tool == "ruff":
        return _parse_ruff(text, workspace)
    if tool == "mypy":
        return _parse_mypy(text, workspace)
    if tool == "bandit":
        return _parse_bandit(text, workspace)
    if tool == "semgrep":
        return _parse_semgrep(text, workspace)
    if tool == "eslint":
        return _parse_eslint(text, workspace)
    if tool == "pytest":
        return _parse_pytest(text, workspace)
    return []


def _parse_ruff(text: str, workspace: Path) -> list[ToolDiagnostic]:
    data = _json_value(text)
    if not isinstance(data, list):
        return []
    diagnostics: list[ToolDiagnostic] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_location = item.get("location")
        location = raw_location if isinstance(raw_location, dict) else {}
        diagnostics.append(
            ToolDiagnostic(
                severity="error",
                message=str(item.get("message") or "Ruff diagnostic"),
                file=_relative_path(str(item.get("filename") or ""), workspace),
                line=_as_int(location.get("row")),
                column=_as_int(location.get("column")),
                code=str(item.get("code") or ""),
            )
        )
    return diagnostics


def _parse_mypy(text: str, workspace: Path) -> list[ToolDiagnostic]:
    diagnostics: list[ToolDiagnostic] = []
    for line in text.splitlines():
        match = _MYPY_LINE.match(line.strip())
        if match is None:
            continue
        diagnostics.append(
            ToolDiagnostic(
                severity=match.group("severity"),
                message=match.group("message"),
                file=_relative_path(match.group("file"), workspace),
                line=int(match.group("line")),
                column=int(match.group("column") or 0),
                code=match.group("code") or "",
            )
        )
    return diagnostics


def _parse_bandit(text: str, workspace: Path) -> list[ToolDiagnostic]:
    data = _json_value(text)
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [
        ToolDiagnostic(
            severity=str(item.get("issue_severity") or "warning").lower(),
            message=str(item.get("issue_text") or "Bandit diagnostic"),
            file=_relative_path(str(item.get("filename") or ""), workspace),
            line=_as_int(item.get("line_number")),
            code=str(item.get("test_id") or ""),
        )
        for item in items
        if isinstance(item, dict)
    ]


def _parse_semgrep(text: str, workspace: Path) -> list[ToolDiagnostic]:
    data = _json_value(text)
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    diagnostics: list[ToolDiagnostic] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_start = item.get("start")
        raw_extra = item.get("extra")
        start = raw_start if isinstance(raw_start, dict) else {}
        extra = raw_extra if isinstance(raw_extra, dict) else {}
        diagnostics.append(
            ToolDiagnostic(
                severity=str(extra.get("severity") or "warning").lower(),
                message=str(extra.get("message") or "Semgrep diagnostic"),
                file=_relative_path(str(item.get("path") or ""), workspace),
                line=_as_int(start.get("line")),
                column=_as_int(start.get("col")),
                code=str(item.get("check_id") or ""),
            )
        )
    return diagnostics


def _parse_eslint(text: str, workspace: Path) -> list[ToolDiagnostic]:
    data = _json_value(text)
    if not isinstance(data, list):
        return []
    diagnostics: list[ToolDiagnostic] = []
    for file_result in data:
        if not isinstance(file_result, dict):
            continue
        messages = file_result.get("messages")
        if not isinstance(messages, list):
            continue
        for item in messages:
            if not isinstance(item, dict):
                continue
            diagnostics.append(
                ToolDiagnostic(
                    severity="error" if _as_int(item.get("severity")) >= 2 else "warning",
                    message=str(item.get("message") or "ESLint diagnostic"),
                    file=_relative_path(str(file_result.get("filePath") or ""), workspace),
                    line=_as_int(item.get("line")),
                    column=_as_int(item.get("column")),
                    code=str(item.get("ruleId") or ""),
                )
            )
    return diagnostics


def _parse_pytest(text: str, workspace: Path) -> list[ToolDiagnostic]:
    diagnostics: list[ToolDiagnostic] = []
    for line in text.splitlines():
        match = re.match(r"^(?P<file>.+?\.py):(?P<line>\d+):\s*(?P<message>.+)$", line.strip())
        if match is None:
            continue
        diagnostics.append(
            ToolDiagnostic(
                severity="error",
                message=match.group("message"),
                file=_relative_path(match.group("file"), workspace),
                line=int(match.group("line")),
                code="pytest",
            )
        )
    return diagnostics


def _relative_path(value: str, workspace: Path) -> str:
    if not value:
        return ""
    path = Path(value)
    try:
        return (
            path.resolve().relative_to(workspace).as_posix()
            if path.is_absolute()
            else path.as_posix()
        )
    except ValueError:
        return path.as_posix()


def _bounded(value: str, max_chars: int) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True


def _json_value(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    data = _json_value(_read_text(path))
    return data if isinstance(data, dict) else {}


def _package_dependencies(package: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            names.update(str(name) for name in value)
    return names


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0
