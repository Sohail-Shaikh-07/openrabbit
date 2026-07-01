"""Unit tests for scripts/smoke_test.py helper functions (OP-35).

The smoke_test module is a standalone script; these tests exercise its
importable helpers without spawning the full CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add the scripts directory to sys.path so we can import smoke_test.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import smoke_test

# ---------------------------------------------------------------------------
# SmokeResult
# ---------------------------------------------------------------------------


def test_smoke_result_passed_true() -> None:
    r = smoke_test.SmokeResult(label="version", passed=True, output="openrabbit 0.1.0")
    assert r.passed is True


def test_smoke_result_passed_false() -> None:
    r = smoke_test.SmokeResult(label="version", passed=False, output="error")
    assert r.passed is False


def test_smoke_result_to_dict_keys() -> None:
    r = smoke_test.SmokeResult(label="help", passed=True, output="Usage:")
    d = r.to_dict()
    assert set(d) == {"label", "passed", "output"}


def test_smoke_result_to_dict_values() -> None:
    r = smoke_test.SmokeResult(label="init", passed=False, output="fail")
    d = r.to_dict()
    assert d["label"] == "init"
    assert d["passed"] is False
    assert d["output"] == "fail"


# ---------------------------------------------------------------------------
# SmokeReport
# ---------------------------------------------------------------------------


def test_smoke_report_all_passed_true() -> None:
    results = [
        smoke_test.SmokeResult("a", True, ""),
        smoke_test.SmokeResult("b", True, ""),
    ]
    report = smoke_test.SmokeReport(results)
    assert report.all_passed is True


def test_smoke_report_all_passed_false_when_any_fail() -> None:
    results = [
        smoke_test.SmokeResult("a", True, ""),
        smoke_test.SmokeResult("b", False, "boom"),
    ]
    report = smoke_test.SmokeReport(results)
    assert report.all_passed is False


def test_smoke_report_passed_count() -> None:
    results = [
        smoke_test.SmokeResult("a", True, ""),
        smoke_test.SmokeResult("b", False, ""),
        smoke_test.SmokeResult("c", True, ""),
    ]
    report = smoke_test.SmokeReport(results)
    assert report.passed_count == 2


def test_smoke_report_failed_count() -> None:
    results = [
        smoke_test.SmokeResult("a", True, ""),
        smoke_test.SmokeResult("b", False, ""),
    ]
    report = smoke_test.SmokeReport(results)
    assert report.failed_count == 1


def test_smoke_report_to_dict_includes_results() -> None:
    results = [smoke_test.SmokeResult("x", True, "ok")]
    report = smoke_test.SmokeReport(results)
    d = report.to_dict()
    assert "results" in d
    assert d["all_passed"] is True
    assert d["passed_count"] == 1
    assert d["failed_count"] == 0


# ---------------------------------------------------------------------------
# run_check
# ---------------------------------------------------------------------------


def test_run_check_success() -> None:
    with patch("smoke_test.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = smoke_test.run_check("version", [sys.executable, "--version"])
    assert result.passed is True
    assert result.label == "version"


def test_run_check_failure() -> None:
    with patch("smoke_test.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad")
        result = smoke_test.run_check("version", [sys.executable, "--version"])
    assert result.passed is False


def test_run_check_exception() -> None:
    with patch("smoke_test.subprocess.run", side_effect=FileNotFoundError("not found")):
        result = smoke_test.run_check("missing", ["nonexistent-binary"])
    assert result.passed is False
    assert "not found" in result.output


def test_run_check_output_contains_stdout() -> None:
    with patch("smoke_test.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="hello world", stderr="")
        result = smoke_test.run_check("greet", ["echo", "hello world"])
    assert "hello world" in result.output


# ---------------------------------------------------------------------------
# build_checks
# ---------------------------------------------------------------------------


def test_build_checks_returns_list() -> None:
    checks = smoke_test.build_checks(install_dir=Path("."))
    assert isinstance(checks, list)
    assert len(checks) > 0


def test_build_checks_each_item_is_tuple() -> None:
    checks = smoke_test.build_checks(install_dir=Path("."))
    for label, cmd in checks:
        assert isinstance(label, str)
        assert isinstance(cmd, list)


def test_build_checks_includes_version() -> None:
    checks = smoke_test.build_checks(install_dir=Path("."))
    labels = [label for label, _ in checks]
    assert any("version" in label for label in labels)


def test_build_checks_includes_help() -> None:
    checks = smoke_test.build_checks(install_dir=Path("."))
    labels = [label for label, _ in checks]
    assert any("help" in label for label in labels)


def test_build_checks_includes_init() -> None:
    checks = smoke_test.build_checks(install_dir=Path("."))
    labels = [label for label, _ in checks]
    assert any("init" in label for label in labels)
