"""Tests for the PR evaluation/test-log command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.commands.eval import parse_pr_numbers, parse_scenario_groups, run_eval
from cli.main import app
from configs import load_settings

runner = CliRunner()


def test_parse_pr_numbers_accepts_comma_and_space_separated_values() -> None:
    assert parse_pr_numbers("1, 2 3") == [1, 2, 3]


def test_parse_pr_numbers_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        parse_pr_numbers("1, nope")


def test_parse_scenario_groups_accepts_named_pr_sets() -> None:
    assert parse_scenario_groups(["security=1, 4", "quality=2 3"], [1, 2, 3, 4]) == {
        "security": [1, 4],
        "quality": [2, 3],
    }


def test_parse_scenario_groups_defaults_to_all_selected_prs() -> None:
    assert parse_scenario_groups(None, [1, 2]) == {"default": [1, 2]}


def test_parse_scenario_groups_rejects_invalid_specs() -> None:
    with pytest.raises(ValueError, match="NAME=1,2"):
        parse_scenario_groups(["missing-equals"], [1])
    with pytest.raises(ValueError, match="positive integers"):
        parse_scenario_groups(["quality=0"], [1])
    with pytest.raises(ValueError, match="selected PRs"):
        parse_scenario_groups(["quality=9"], [1])


@pytest.mark.asyncio
async def test_run_eval_writes_json_and_markdown_reports(
    scaffold_repo: Path,
    tmp_path: Path,
) -> None:
    settings = load_settings(scaffold_repo, env={})
    output = tmp_path / "eval.json"
    markdown = tmp_path / "eval.md"

    async def fake_review_runner(
        *_args: object, number: int, **_kwargs: object
    ) -> dict[str, object]:
        return {
            "repo": "o/r",
            "number": number,
            "title": f"PR {number}",
            "head_sha": f"sha-{number}",
            "findings": (
                [
                    {"category": "security"},
                    {"category": "tests"},
                ]
                if number == 1
                else []
            ),
            "dropped_findings_count": 1 if number == 1 else 0,
            "skipped_paths_count": 2 if number == 2 else 0,
            "context_loaded": number == 1,
            "memory_context": "loaded" if number == 1 else "disabled",
            "learning_count": 2 if number == 1 else 0,
            "guideline_sources": ["AGENTS.md"] if number == 1 else [],
            "linked_issue_count": 1 if number == 1 else 0,
            "quality_gates": (
                [
                    {
                        "tool": "ruff",
                        "status": "failed",
                        "diagnostics_count": 2,
                        "diagnostics": [
                            {
                                "severity": "error",
                                "file": "src/app.py",
                                "line": 3,
                                "message": "Undefined name",
                            }
                        ],
                    }
                ]
                if number == 1
                else []
            ),
            "quality_status_counts": {"failed": 1} if number == 1 else {},
            "quality_diagnostics_count": 2 if number == 1 else 0,
        }

    report = await run_eval(
        settings,
        repo="o/r",
        prs=[1, 2],
        output=output,
        markdown=markdown,
        review_runner=fake_review_runner,
    )

    assert output.is_file()
    assert markdown.is_file()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["repo"] == "o/r"
    assert data["provider"] == settings.model.provider
    assert data["scenario_groups"] == [{"name": "default", "prs": [1, 2]}]
    assert data["totals"]["prs"] == 2
    assert data["totals"]["findings"] == 2
    assert data["totals"]["learnings"] == 2
    assert data["totals"]["linked_issues"] == 1
    assert data["totals"]["guideline_sources"] == ["AGENTS.md"]
    assert data["runs"][0]["command"] == "openrabbit review --pr 1 --repo o/r --dry-run"
    assert data["runs"][0]["scenario_group"] == "default"
    assert data["runs"][0]["context_mode"] == "loaded"
    assert data["runs"][0]["memory_context"] == "loaded"
    assert data["runs"][0]["learning_count"] == 2
    assert data["runs"][0]["guideline_sources"] == ["AGENTS.md"]
    assert data["runs"][0]["linked_issue_count"] == 1
    assert data["runs"][0]["categories"] == {"security": 1, "tests": 1}
    assert data["runs"][0]["quality_gates"][0]["tool"] == "ruff"
    assert data["runs"][0]["quality_status_counts"] == {"failed": 1}
    assert data["totals"]["quality_diagnostics"] == 2
    assert data["totals"]["quality_status_counts"] == {"failed": 1}
    assert data["runs"][1]["skipped_paths_count"] == 2
    assert data["runs"][1]["scenario_group"] == "default"
    assert data["dashboard"]["cards"]["prs"] == 2
    assert data["dashboard"]["charts"]["findings_by_pr"] == [
        {"pr": 1, "findings": 2, "scenario_group": "default"},
        {"pr": 2, "findings": 0, "scenario_group": "default"},
    ]
    assert data["command_outcomes"]["successes"] == 2
    assert data["command_outcomes"]["failures"] == 0
    assert data["context_sources"]["context_modes"] == {"loaded": 1, "diff only": 1}
    assert data["context_sources"]["memory_contexts"] == {"loaded": 1, "disabled": 1}
    assert data["tool_findings"]["tools"]["ruff"]["diagnostics"] == 2
    assert report["output_path"] == str(output)
    markdown_text = markdown.read_text(encoding="utf-8")
    assert "OpenRabbit Evaluation Report" in markdown_text
    assert "## Dashboard Summary" in markdown_text
    assert "## Scenario Groups" in markdown_text
    assert "## Context Sources" in markdown_text
    assert "## Tool Findings" in markdown_text


@pytest.mark.asyncio
async def test_run_eval_compares_baseline_and_checks_expectations(
    scaffold_repo: Path,
    tmp_path: Path,
) -> None:
    settings = load_settings(scaffold_repo, env={})
    output = tmp_path / "eval.json"
    markdown = tmp_path / "eval.md"
    baseline = tmp_path / "baseline.json"
    expectations = tmp_path / "expectations.json"
    baseline.write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "totals": {
                    "findings": 1,
                    "failures": 0,
                    "dropped_findings": 0,
                    "skipped_paths": 0,
                    "runtime_ms": 30.0,
                },
                "runs": [
                    {
                        "pr": 1,
                        "findings_count": 1,
                        "runtime_ms": 30.0,
                        "failure": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    expectations.write_text(
        json.dumps(
            {
                "expectations": [
                    {"pr": 1, "min_findings": 2, "categories": {"security": 1}},
                    {"pr": 2, "max_findings": 0},
                ]
            }
        ),
        encoding="utf-8",
    )

    async def fake_review_runner(
        *_args: object, number: int, **_kwargs: object
    ) -> dict[str, object]:
        return {
            "repo": "o/r",
            "number": number,
            "title": f"PR {number}",
            "head_sha": f"sha-{number}",
            "findings": (
                [
                    {"category": "security"},
                    {"category": "tests"},
                ]
                if number == 1
                else []
            ),
            "context_loaded": False,
        }

    report = await run_eval(
        settings,
        repo="o/r",
        prs=[1, 2],
        output=output,
        markdown=markdown,
        compare=baseline,
        expectations=expectations,
        review_runner=fake_review_runner,
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["comparison"]["totals_delta"]["findings"] == 1
    assert data["comparison"]["runs"][0]["pr"] == 1
    assert data["comparison"]["runs"][0]["findings_delta"] == 1
    assert data["comparison"]["runs"][1]["status"] == "new"
    assert data["assertions"]["passed"] == 2
    assert data["assertions"]["failed"] == 0
    assert data["dashboard"]["trend"]["totals_delta"]["findings"] == 1
    assert data["dashboard"]["trend"]["runs"][0]["pr"] == 1
    assert report["assertions"]["items"][0]["checks"][0]["name"] == "min_findings"
    text = markdown.read_text(encoding="utf-8")
    assert "Trend Comparison" in text
    assert "Expected Finding Assertions" in text


@pytest.mark.asyncio
async def test_run_eval_records_failures(scaffold_repo: Path, tmp_path: Path) -> None:
    settings = load_settings(scaffold_repo, env={})

    async def failing_review_runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("GitHub unavailable")

    report = await run_eval(
        settings,
        repo="o/r",
        prs=[1],
        output=tmp_path / "eval.json",
        markdown=tmp_path / "eval.md",
        review_runner=failing_review_runner,
    )

    assert report["totals"]["failures"] == 1
    assert report["runs"][0]["failure"] == "GitHub unavailable"
    assert report["runs"][0]["scenario_group"] == "default"
    assert report["runs"][0]["findings_count"] == 0
    assert report["runs"][0]["memory_context"] == "unknown"
    assert report["runs"][0]["learning_count"] == 0
    assert report["runs"][0]["guideline_sources"] == []
    assert report["runs"][0]["linked_issue_count"] == 0
    assert report["command_outcomes"]["failures"] == 1
    assert report["command_outcomes"]["failed_runs"][0]["pr"] == 1
    assert report["dashboard"]["cards"]["failures"] == 1
    assert report["markdown_path"] == str(tmp_path / "eval.md")
    assert "No local quality tool findings recorded." in (tmp_path / "eval.md").read_text(
        encoding="utf-8"
    )


def test_eval_cli_command_exists() -> None:
    result = runner.invoke(app, ["eval", "--help"])

    assert result.exit_code == 0
    assert "evaluation" in result.output.lower()
    assert "compare" in result.output
    assert "expectations" in result.output
