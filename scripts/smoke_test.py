"""Cross-platform smoke test for the OpenRabbit CLI (OP-35).

Run this script after installing the package to verify that the three
core CLI entry points work on the current platform:

    python scripts/smoke_test.py

Exit code is 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SmokeResult:
    """Outcome of a single smoke check."""

    label: str
    passed: bool
    output: str

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "passed": self.passed, "output": self.output}


@dataclass
class SmokeReport:
    """Aggregated outcome of a full smoke run."""

    results: list[SmokeResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "results": [r.to_dict() for r in self.results],
        }


def run_check(label: str, cmd: list[str]) -> SmokeResult:
    """Run one subprocess check and return a SmokeResult."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = proc.stdout or proc.stderr
        return SmokeResult(label=label, passed=proc.returncode == 0, output=output.strip())
    except Exception as exc:
        return SmokeResult(label=label, passed=False, output=str(exc))


def build_checks(install_dir: Path) -> list[tuple[str, list[str]]]:
    """Return the list of (label, command) pairs to execute.

    ``install_dir`` is a temporary directory used for the ``init`` check so
    we do not write into the caller's working tree.
    """
    openrabbit = [sys.executable, "-m", "cli.main"]
    return [
        ("version", [*openrabbit, "--version"]),
        ("help", [*openrabbit, "--help"]),
        ("init", [*openrabbit, "init", "--path", str(install_dir)]),
    ]


def run_smoke(verbose: bool = True) -> SmokeReport:
    """Run all smoke checks and return the report."""
    report = SmokeReport()
    with tempfile.TemporaryDirectory() as tmp:
        for label, cmd in build_checks(install_dir=Path(tmp)):
            result = run_check(label, cmd)
            report.results.append(result)
            if verbose:
                status = "PASS" if result.passed else "FAIL"
                print(f"  [{status}] {label}")
                if not result.passed:
                    print(f"         {result.output[:200]}")
    return report


def main() -> int:
    print("OpenRabbit smoke test")
    print(f"Python {sys.version}")
    print(f"Platform: {sys.platform}")
    print()
    report = run_smoke(verbose=True)
    print()
    if report.all_passed:
        print(f"All {report.passed_count} checks passed.")
        return 0
    print(f"{report.failed_count} of {len(report.results)} checks failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
