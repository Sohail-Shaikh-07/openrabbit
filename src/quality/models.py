"""Structured local quality-gate results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ToolStatus(StrEnum):
    """Outcome of one local quality-gate command."""

    passed = "passed"
    failed = "failed"
    timed_out = "timed_out"
    unavailable = "unavailable"
    error = "error"


@dataclass(frozen=True)
class ToolDiagnostic:
    """One normalized diagnostic emitted by a local tool."""

    severity: str
    message: str
    file: str = ""
    line: int = 0
    column: int = 0
    code: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "code": self.code,
        }


@dataclass(frozen=True)
class ToolRunResult:
    """Sanitized result for one supported local tool."""

    tool: str
    status: ToolStatus
    command: tuple[str, ...]
    exit_code: int | None
    duration_ms: float
    summary: str
    diagnostics: tuple[ToolDiagnostic, ...] = ()
    output_truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        public_command = list(self.command)
        if public_command:
            public_command[0] = Path(public_command[0]).name
        return {
            "tool": self.tool,
            "status": self.status.value,
            "command": public_command,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 2),
            "summary": self.summary,
            "diagnostics_count": len(self.diagnostics),
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "output_truncated": self.output_truncated,
        }
