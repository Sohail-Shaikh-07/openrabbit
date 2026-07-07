"""Evaluation/test-log command for repeatable PR review runs."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from cli.commands.review import ReviewMode, run_review
from cli.commands.start import resolve_target_repo
from configs.settings import Settings

ReviewRunner = Callable[..., Awaitable[dict[str, object]]]

_DEFAULT_PRS = (1, 2, 3, 4, 5)


def parse_pr_numbers(value: str) -> list[int]:
    """Parse comma or whitespace separated PR numbers."""
    parts = [part for part in re.split(r"[\s,]+", value.strip()) if part]
    if not parts:
        raise ValueError("PR numbers must be positive integers")
    numbers: list[int] = []
    for part in parts:
        try:
            number = int(part)
        except ValueError as exc:
            raise ValueError("PR numbers must be positive integers") from exc
        if number <= 0:
            raise ValueError("PR numbers must be positive integers")
        numbers.append(number)
    return numbers


async def run_eval(
    settings: Settings,
    *,
    repo: str | None,
    prs: list[int] | None,
    output: Path,
    markdown: Path | None,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
) -> dict[str, object]:
    """Run dry-run reviews for selected PRs and write evaluation reports."""
    target_repo = resolve_target_repo(settings, repo)
    runner = review_runner or run_review
    generated_at = datetime.now(UTC).isoformat()
    pr_numbers = prs or list(_DEFAULT_PRS)
    runs: list[dict[str, object]] = []

    for number in pr_numbers:
        started = time.perf_counter()
        command = f"openrabbit review --pr {number} --repo {target_repo} --dry-run"
        try:
            summary = await runner(
                settings,
                number=number,
                repo=target_repo,
                env=env,
                dry_run=True,
                mode=ReviewMode.INCREMENTAL,
            )
            runtime_ms = (time.perf_counter() - started) * 1000
            runs.append(
                _run_record_from_summary(
                    summary,
                    command=command,
                    repo=target_repo,
                    provider=settings.model.provider,
                    model_name=settings.model.model_name,
                    runtime_ms=runtime_ms,
                    failure=None,
                )
            )
        except Exception as exc:
            runtime_ms = (time.perf_counter() - started) * 1000
            runs.append(
                {
                    "command": command,
                    "repo": target_repo,
                    "pr": number,
                    "provider": settings.model.provider,
                    "model_name": settings.model.model_name,
                    "context_mode": "unknown",
                    "findings_count": 0,
                    "categories": {},
                    "dropped_findings_count": 0,
                    "skipped_paths_count": 0,
                    "runtime_ms": round(runtime_ms, 2),
                    "failure": str(exc),
                }
            )

    report: dict[str, object] = {
        "schema_version": 1,
        "generated_at": generated_at,
        "repo": target_repo,
        "provider": settings.model.provider,
        "model_name": settings.model.model_name,
        "prs": pr_numbers,
        "totals": _totals(runs),
        "runs": runs,
    }

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if markdown is not None:
        markdown = markdown.resolve()
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(_markdown_report(report), encoding="utf-8")

    report["output_path"] = str(output)
    report["markdown_path"] = str(markdown) if markdown is not None else None
    return report


def run_eval_blocking(
    settings: Settings,
    *,
    repo: str | None,
    prs: list[int] | None,
    output: Path,
    markdown: Path | None,
) -> dict[str, object]:
    """Synchronous wrapper for Typer."""
    return asyncio.run(run_eval(settings, repo=repo, prs=prs, output=output, markdown=markdown))


def render_eval_summary(report: dict[str, object], out: TextIO) -> None:
    """Print a compact eval command summary."""
    totals = _object_dict(report.get("totals"))
    print("OpenRabbit evaluation complete", file=out)
    print(f"  Repo:      {report.get('repo')}", file=out)
    print(f"  PRs:       {totals.get('prs', 0)}", file=out)
    print(f"  Findings:  {totals.get('findings', 0)}", file=out)
    print(f"  Failures:  {totals.get('failures', 0)}", file=out)
    print(f"  JSON:      {report.get('output_path')}", file=out)
    markdown = report.get("markdown_path")
    if markdown:
        print(f"  Markdown:  {markdown}", file=out)


def _run_record_from_summary(
    summary: dict[str, object],
    *,
    command: str,
    repo: str,
    provider: str,
    model_name: str,
    runtime_ms: float,
    failure: str | None,
) -> dict[str, object]:
    findings = summary.get("findings")
    finding_items = findings if isinstance(findings, list) else []
    return {
        "command": command,
        "repo": repo,
        "pr": _int_summary(summary, "number"),
        "title": str(summary.get("title", "")),
        "head_sha": str(summary.get("head_sha", "")),
        "provider": provider,
        "model_name": model_name,
        "context_mode": "loaded" if summary.get("context_loaded") is True else "diff only",
        "findings_count": len(finding_items),
        "categories": _categories(finding_items),
        "dropped_findings_count": _int_summary(summary, "dropped_findings_count"),
        "skipped_paths_count": _int_summary(summary, "skipped_paths_count"),
        "runtime_ms": round(runtime_ms, 2),
        "failure": failure,
    }


def _categories(findings: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in findings:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "unknown") or "unknown")
        counts[category] = counts.get(category, 0) + 1
    return counts


def _totals(runs: list[dict[str, object]]) -> dict[str, object]:
    categories: dict[str, int] = {}
    for run in runs:
        raw_categories = run.get("categories")
        if isinstance(raw_categories, dict):
            for category, count in raw_categories.items():
                categories[str(category)] = categories.get(str(category), 0) + _coerce_int(count)
    return {
        "prs": len(runs),
        "findings": sum(_int_run(run, "findings_count") for run in runs),
        "dropped_findings": sum(_int_run(run, "dropped_findings_count") for run in runs),
        "skipped_paths": sum(_int_run(run, "skipped_paths_count") for run in runs),
        "failures": sum(1 for run in runs if run.get("failure")),
        "categories": categories,
        "runtime_ms": round(sum(_float_run(run, "runtime_ms") for run in runs), 2),
    }


def _markdown_report(report: dict[str, object]) -> str:
    totals = _object_dict(report.get("totals"))
    lines = [
        "# OpenRabbit Evaluation Report",
        "",
        f"- Repository: `{report.get('repo')}`",
        f"- Provider: `{report.get('provider')}`",
        f"- Model: `{report.get('model_name')}`",
        f"- PRs: {totals.get('prs', 0)}",
        f"- Findings: {totals.get('findings', 0)}",
        f"- Failures: {totals.get('failures', 0)}",
        "",
        "| PR | Context | Findings | Categories | Dropped | Skipped | Runtime ms | Failure |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    runs = report.get("runs")
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            lines.append(
                "| {pr} | {context} | {findings} | {categories} | {dropped} | "
                "{skipped} | {runtime} | {failure} |".format(
                    pr=run.get("pr", ""),
                    context=run.get("context_mode", ""),
                    findings=run.get("findings_count", 0),
                    categories=_category_text(run.get("categories")),
                    dropped=run.get("dropped_findings_count", 0),
                    skipped=run.get("skipped_paths_count", 0),
                    runtime=run.get("runtime_ms", 0),
                    failure=str(run.get("failure") or ""),
                )
            )
    return "\n".join(lines) + "\n"


def _category_text(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return ", ".join(f"{key}:{count}" for key, count in sorted(value.items()))


def _int_summary(summary: dict[str, object], key: str) -> int:
    value = summary.get(key)
    return _coerce_int(value)


def _int_run(run: dict[str, object], key: str) -> int:
    value = run.get(key)
    return _coerce_int(value)


def _float_run(run: dict[str, object], key: str) -> float:
    value = run.get(key)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _coerce_int(value: object) -> int:
    if isinstance(value, int):
        return value
    return 0


def _object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}
