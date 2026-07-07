"""Tests for the PR evaluation/test-log command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.commands.eval import parse_pr_numbers, run_eval
from cli.main import app
from configs import load_settings

runner = CliRunner()


def test_parse_pr_numbers_accepts_comma_and_space_separated_values() -> None:
    assert parse_pr_numbers("1, 2 3") == [1, 2, 3]


def test_parse_pr_numbers_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        parse_pr_numbers("1, nope")


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
    assert data["totals"]["prs"] == 2
    assert data["totals"]["findings"] == 2
    assert data["runs"][0]["command"] == "openrabbit review --pr 1 --repo o/r --dry-run"
    assert data["runs"][0]["context_mode"] == "loaded"
    assert data["runs"][0]["categories"] == {"security": 1, "tests": 1}
    assert data["runs"][1]["skipped_paths_count"] == 2
    assert report["output_path"] == str(output)
    assert "OpenRabbit Evaluation Report" in markdown.read_text(encoding="utf-8")


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
        markdown=None,
        review_runner=failing_review_runner,
    )

    assert report["totals"]["failures"] == 1
    assert report["runs"][0]["failure"] == "GitHub unavailable"
    assert report["runs"][0]["findings_count"] == 0


def test_eval_cli_command_exists() -> None:
    result = runner.invoke(app, ["eval", "--help"])

    assert result.exit_code == 0
    assert "evaluation" in result.output.lower()
