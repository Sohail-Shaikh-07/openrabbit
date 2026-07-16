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


def parse_scenario_groups(
    values: list[str] | None,
    selected_prs: list[int],
) -> dict[str, list[int]]:
    """Parse named scenario groups from NAME=1,2 strings."""
    if not values:
        return {"default": list(selected_prs)}

    selected = set(selected_prs)
    groups: dict[str, list[int]] = {}
    for value in values:
        name, separator, raw_numbers = value.partition("=")
        name = name.strip()
        if not separator or not name or not raw_numbers.strip():
            raise ValueError("scenario groups must use NAME=1,2 format")
        numbers = parse_pr_numbers(raw_numbers)
        unknown = [number for number in numbers if number not in selected]
        if unknown:
            raise ValueError("scenario group PRs must be included in the selected PRs")
        groups[name] = numbers
    return groups


async def run_eval(
    settings: Settings,
    *,
    repo: str | None,
    prs: list[int] | None,
    output: Path,
    markdown: Path | None,
    compare: Path | None = None,
    expectations: Path | None = None,
    scenario_groups: dict[str, list[int]] | None = None,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
) -> dict[str, object]:
    """Run dry-run reviews for selected PRs and write evaluation reports."""
    target_repo = resolve_target_repo(settings, repo)
    runner = review_runner or run_review
    generated_at = datetime.now(UTC).isoformat()
    pr_numbers = prs or list(_DEFAULT_PRS)
    groups = scenario_groups or parse_scenario_groups(None, pr_numbers)
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
                    scenario_group=_scenario_group_for_pr(number, groups),
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
                    "scenario_group": _scenario_group_for_pr(number, groups),
                    "context_mode": "unknown",
                    "findings_count": 0,
                    "categories": {},
                    "dropped_findings_count": 0,
                    "skipped_paths_count": 0,
                    "memory_context": "unknown",
                    "learning_count": 0,
                    "guideline_sources": [],
                    "linked_issue_count": 0,
                    "quality_gates": [],
                    "quality_status_counts": {},
                    "quality_diagnostics_count": 0,
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
        "scenario_groups": _scenario_group_records(groups),
        "totals": _totals(runs),
        "runs": runs,
    }

    if compare is not None:
        report["comparison"] = _compare_reports(
            report,
            baseline=_load_json_object(compare),
            baseline_path=compare,
        )
    if expectations is not None:
        report["assertions"] = _assert_expectations(
            report,
            expectations=_load_expectations(expectations),
            expectations_path=expectations,
        )

    report["command_outcomes"] = _command_outcomes(runs)
    report["context_sources"] = _context_sources(runs)
    report["tool_findings"] = _tool_findings(runs)
    report["dashboard"] = _dashboard_summary(report)

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
    compare: Path | None = None,
    expectations: Path | None = None,
    scenario_groups: dict[str, list[int]] | None = None,
) -> dict[str, object]:
    """Synchronous wrapper for Typer."""
    return asyncio.run(
        run_eval(
            settings,
            repo=repo,
            prs=prs,
            output=output,
            markdown=markdown,
            compare=compare,
            expectations=expectations,
            scenario_groups=scenario_groups,
        )
    )


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
    assertions = _object_dict(report.get("assertions"))
    if assertions:
        print(
            f"  Assertions: {assertions.get('passed', 0)} passed, "
            f"{assertions.get('failed', 0)} failed",
            file=out,
        )
    comparison = _object_dict(report.get("comparison"))
    if comparison:
        print(f"  Compared:  {comparison.get('baseline_path')}", file=out)


def _run_record_from_summary(
    summary: dict[str, object],
    *,
    command: str,
    repo: str,
    provider: str,
    model_name: str,
    scenario_group: str,
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
        "scenario_group": scenario_group,
        "context_mode": "loaded" if summary.get("context_loaded") is True else "diff only",
        "findings_count": len(finding_items),
        "categories": _categories(finding_items),
        "dropped_findings_count": _int_summary(summary, "dropped_findings_count"),
        "skipped_paths_count": _int_summary(summary, "skipped_paths_count"),
        "memory_context": str(summary.get("memory_context", "unknown") or "unknown"),
        "learning_count": _int_summary(summary, "learning_count"),
        "guideline_sources": _string_list(summary.get("guideline_sources")),
        "linked_issue_count": _int_summary(summary, "linked_issue_count"),
        "quality_gates": _dict_list(summary.get("quality_gates")),
        "quality_status_counts": _object_dict(summary.get("quality_status_counts")),
        "quality_diagnostics_count": _int_summary(summary, "quality_diagnostics_count"),
        "runtime_ms": round(runtime_ms, 2),
        "failure": failure,
    }


def _scenario_group_for_pr(pr_number: int, groups: dict[str, list[int]]) -> str:
    matches = [name for name, numbers in groups.items() if pr_number in numbers]
    return matches[0] if matches else "ungrouped"


def _scenario_group_records(groups: dict[str, list[int]]) -> list[dict[str, object]]:
    return [{"name": name, "prs": list(numbers)} for name, numbers in sorted(groups.items())]


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
    quality_statuses: dict[str, int] = {}
    for run in runs:
        raw_categories = run.get("categories")
        if isinstance(raw_categories, dict):
            for category, count in raw_categories.items():
                categories[str(category)] = categories.get(str(category), 0) + _coerce_int(count)
        raw_quality_statuses = run.get("quality_status_counts")
        if isinstance(raw_quality_statuses, dict):
            for status, count in raw_quality_statuses.items():
                quality_statuses[str(status)] = quality_statuses.get(str(status), 0) + _coerce_int(
                    count
                )
    return {
        "prs": len(runs),
        "findings": sum(_int_run(run, "findings_count") for run in runs),
        "dropped_findings": sum(_int_run(run, "dropped_findings_count") for run in runs),
        "skipped_paths": sum(_int_run(run, "skipped_paths_count") for run in runs),
        "learnings": sum(_int_run(run, "learning_count") for run in runs),
        "linked_issues": sum(_int_run(run, "linked_issue_count") for run in runs),
        "guideline_sources": sorted(
            {source for run in runs for source in _string_list(run.get("guideline_sources"))}
        ),
        "failures": sum(1 for run in runs if run.get("failure")),
        "categories": categories,
        "quality_diagnostics": sum(_int_run(run, "quality_diagnostics_count") for run in runs),
        "quality_status_counts": quality_statuses,
        "runtime_ms": round(sum(_float_run(run, "runtime_ms") for run in runs), 2),
    }


def _command_outcomes(runs: list[dict[str, object]]) -> dict[str, object]:
    failures = [
        {"pr": run.get("pr"), "command": run.get("command"), "failure": run.get("failure")}
        for run in runs
        if run.get("failure")
    ]
    return {
        "successes": len(runs) - len(failures),
        "failures": len(failures),
        "failed_runs": failures,
    }


def _count_strings(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _context_sources(runs: list[dict[str, object]]) -> dict[str, object]:
    context_modes = _count_strings([str(run.get("context_mode", "unknown")) for run in runs])
    memory_modes = _count_strings([str(run.get("memory_context", "unknown")) for run in runs])
    guideline_sources = sorted(
        {source for run in runs for source in _string_list(run.get("guideline_sources"))}
    )
    return {
        "context_modes": context_modes,
        "memory_contexts": memory_modes,
        "guideline_sources": guideline_sources,
        "guideline_source_count": len(guideline_sources),
        "linked_issue_count": sum(_int_run(run, "linked_issue_count") for run in runs),
        "learning_count": sum(_int_run(run, "learning_count") for run in runs),
    }


def _tool_findings(runs: list[dict[str, object]]) -> dict[str, object]:
    tools: dict[str, dict[str, object]] = {}
    for run in runs:
        for gate in _dict_list(run.get("quality_gates")):
            tool = str(gate.get("tool", "") or "unknown")
            status = str(gate.get("status", "") or "unknown")
            item = tools.setdefault(
                tool,
                {"runs": 0, "diagnostics": 0, "statuses": {}},
            )
            item["runs"] = _coerce_int(item.get("runs")) + 1
            item["diagnostics"] = _coerce_int(item.get("diagnostics")) + _coerce_int(
                gate.get("diagnostics_count")
            )
            statuses = _object_dict(item.get("statuses"))
            statuses[status] = _coerce_int(statuses.get(status)) + 1
            item["statuses"] = statuses
    return {"tools": tools}


def _dashboard_summary(report: dict[str, object]) -> dict[str, object]:
    totals = _object_dict(report.get("totals"))
    raw_runs = report.get("runs")
    runs = [run for run in raw_runs if isinstance(run, dict)] if isinstance(raw_runs, list) else []
    context_sources = _context_sources(runs)
    dashboard: dict[str, object] = {
        "cards": {
            "prs": _coerce_int(totals.get("prs")),
            "findings": _coerce_int(totals.get("findings")),
            "failures": _coerce_int(totals.get("failures")),
            "dropped_findings": _coerce_int(totals.get("dropped_findings")),
            "quality_diagnostics": _coerce_int(totals.get("quality_diagnostics")),
            "runtime_ms": _coerce_float(totals.get("runtime_ms")),
        },
        "charts": {
            "findings_by_pr": [
                {
                    "pr": run.get("pr"),
                    "findings": _int_run(run, "findings_count"),
                    "scenario_group": run.get("scenario_group", "ungrouped"),
                }
                for run in runs
            ],
            "runtime_by_pr": [
                {
                    "pr": run.get("pr"),
                    "runtime_ms": _float_run(run, "runtime_ms"),
                    "scenario_group": run.get("scenario_group", "ungrouped"),
                }
                for run in runs
            ],
            "context_modes": context_sources.get("context_modes", {}),
            "quality_statuses": totals.get("quality_status_counts", {}),
            "categories": totals.get("categories", {}),
        },
    }
    comparison = _object_dict(report.get("comparison"))
    if comparison:
        dashboard["trend"] = _dashboard_trends(comparison)
    return dashboard


def _dashboard_trends(comparison: dict[str, object]) -> dict[str, object]:
    raw_runs = comparison.get("runs")
    runs = raw_runs if isinstance(raw_runs, list) else []
    return {
        "totals_delta": _object_dict(comparison.get("totals_delta")),
        "runs": [
            {str(key): value for key, value in item.items()}
            for item in runs
            if isinstance(item, dict)
        ],
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
        f"- Quality diagnostics: {totals.get('quality_diagnostics', 0)}",
        "",
        *_markdown_dashboard_sections(report),
        "| PR | Context | Memory | Quality | Diagnostics | Learnings | Guidelines | Linked Issues | Findings | Categories | Dropped | Skipped | Runtime ms | Failure |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    runs = report.get("runs")
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            lines.append(
                "| {pr} | {context} | {memory} | {quality} | {quality_diagnostics} | {learnings} | {guidelines} | "
                "{linked_issues} | {findings} | {categories} | {dropped} | "
                "{skipped} | {runtime} | {failure} |".format(
                    pr=run.get("pr", ""),
                    context=run.get("context_mode", ""),
                    memory=run.get("memory_context", ""),
                    quality=_category_text(run.get("quality_status_counts")),
                    quality_diagnostics=run.get("quality_diagnostics_count", 0),
                    learnings=run.get("learning_count", 0),
                    guidelines=len(_string_list(run.get("guideline_sources"))),
                    linked_issues=run.get("linked_issue_count", 0),
                    findings=run.get("findings_count", 0),
                    categories=_category_text(run.get("categories")),
                    dropped=run.get("dropped_findings_count", 0),
                    skipped=run.get("skipped_paths_count", 0),
                    runtime=run.get("runtime_ms", 0),
                    failure=str(run.get("failure") or ""),
                )
            )
    comparison = _object_dict(report.get("comparison"))
    if comparison:
        lines.extend(_markdown_comparison(comparison))
    assertions = _object_dict(report.get("assertions"))
    if assertions:
        lines.extend(_markdown_assertions(assertions))
    return "\n".join(lines) + "\n"


def _markdown_dashboard_sections(report: dict[str, object]) -> list[str]:
    dashboard = _object_dict(report.get("dashboard"))
    cards = _object_dict(dashboard.get("cards"))
    context_sources = _object_dict(report.get("context_sources"))
    tool_findings = _object_dict(report.get("tool_findings"))
    groups = report.get("scenario_groups")
    lines = [
        "## Dashboard Summary",
        "",
        f"- PRs: {cards.get('prs', 0)}",
        f"- Findings: {cards.get('findings', 0)}",
        f"- Failures: {cards.get('failures', 0)}",
        f"- Runtime ms: {cards.get('runtime_ms', 0)}",
        "",
        "## Scenario Groups",
        "",
    ]
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            prs = group.get("prs", [])
            numbers = ", ".join(str(pr) for pr in prs) if isinstance(prs, list) else ""
            lines.append(f"- {group.get('name')}: {numbers}")
    lines.extend(
        [
            "",
            "## Context Sources",
            "",
            f"- Context modes: {_category_text(context_sources.get('context_modes'))}",
            f"- Memory contexts: {_category_text(context_sources.get('memory_contexts'))}",
            f"- Guideline sources: {context_sources.get('guideline_source_count', 0)}",
            f"- Linked issues: {context_sources.get('linked_issue_count', 0)}",
            "",
            "## Tool Findings",
            "",
        ]
    )
    tools = _object_dict(tool_findings.get("tools"))
    if tools:
        for tool, raw in sorted(tools.items()):
            item = _object_dict(raw)
            lines.append(
                f"- {tool}: diagnostics={item.get('diagnostics', 0)}, "
                f"statuses={_category_text(item.get('statuses'))}"
            )
    else:
        lines.append("- No local quality tool findings recorded.")
    lines.append("")
    return lines


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read JSON file {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse JSON file {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return {str(key): value for key, value in data.items()}


def _load_expectations(path: Path) -> list[dict[str, object]]:
    data = _load_json_object(path)
    raw = data.get("expectations")
    if not isinstance(raw, list):
        raise ValueError("expectations file must contain an expectations list")
    expectations: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each expectation must be an object")
        expectations.append({str(key): value for key, value in item.items()})
    return expectations


def _compare_reports(
    report: dict[str, object],
    *,
    baseline: dict[str, object],
    baseline_path: Path,
) -> dict[str, object]:
    current_totals = _object_dict(report.get("totals"))
    baseline_totals = _object_dict(baseline.get("totals"))
    current_runs = _runs_by_pr(report)
    baseline_runs = _runs_by_pr(baseline)
    pr_numbers = sorted(set(current_runs) | set(baseline_runs))
    return {
        "baseline_path": str(baseline_path.resolve()),
        "baseline_generated_at": str(baseline.get("generated_at", "")),
        "totals_delta": {
            "findings": _coerce_int(current_totals.get("findings"))
            - _coerce_int(baseline_totals.get("findings")),
            "failures": _coerce_int(current_totals.get("failures"))
            - _coerce_int(baseline_totals.get("failures")),
            "dropped_findings": _coerce_int(current_totals.get("dropped_findings"))
            - _coerce_int(baseline_totals.get("dropped_findings")),
            "skipped_paths": _coerce_int(current_totals.get("skipped_paths"))
            - _coerce_int(baseline_totals.get("skipped_paths")),
            "runtime_ms": round(
                _coerce_float(current_totals.get("runtime_ms"))
                - _coerce_float(baseline_totals.get("runtime_ms")),
                2,
            ),
        },
        "runs": [
            _run_delta(pr_number, current_runs.get(pr_number), baseline_runs.get(pr_number))
            for pr_number in pr_numbers
        ],
    }


def _assert_expectations(
    report: dict[str, object],
    *,
    expectations: list[dict[str, object]],
    expectations_path: Path,
) -> dict[str, object]:
    runs = _runs_by_pr(report)
    items = [_assert_one_expectation(expectation, runs) for expectation in expectations]
    failed = sum(1 for item in items if not item.get("passed"))
    return {
        "expectations_path": str(expectations_path.resolve()),
        "passed": len(items) - failed,
        "failed": failed,
        "items": items,
    }


def _assert_one_expectation(
    expectation: dict[str, object],
    runs: dict[int, dict[str, object]],
) -> dict[str, object]:
    pr_number = _coerce_int(expectation.get("pr"))
    if pr_number <= 0:
        raise ValueError("each expectation requires a positive pr number")
    run = runs.get(pr_number)
    checks: list[dict[str, object]] = []
    if run is None:
        checks.append(
            {
                "name": "run_present",
                "passed": False,
                "expected": True,
                "actual": False,
            }
        )
        return {"pr": pr_number, "passed": False, "checks": checks}

    if "min_findings" in expectation:
        checks.append(
            _threshold_check(
                "min_findings",
                actual=_int_run(run, "findings_count"),
                expected=_coerce_int(expectation.get("min_findings")),
                operator=">=",
            )
        )
    if "max_findings" in expectation:
        checks.append(
            _threshold_check(
                "max_findings",
                actual=_int_run(run, "findings_count"),
                expected=_coerce_int(expectation.get("max_findings")),
                operator="<=",
            )
        )
    expected_categories = expectation.get("categories")
    if isinstance(expected_categories, dict):
        actual_categories = _object_dict(run.get("categories"))
        for category, expected in sorted(expected_categories.items()):
            checks.append(
                _threshold_check(
                    f"category:{category}",
                    actual=_coerce_int(actual_categories.get(str(category))),
                    expected=_coerce_int(expected),
                    operator=">=",
                )
            )
    if not checks:
        raise ValueError(f"expectation for PR {pr_number} does not define any checks")
    return {"pr": pr_number, "passed": all(item["passed"] for item in checks), "checks": checks}


def _threshold_check(
    name: str,
    *,
    actual: int,
    expected: int,
    operator: str,
) -> dict[str, object]:
    passed = actual >= expected if operator == ">=" else actual <= expected
    return {
        "name": name,
        "passed": passed,
        "expected": expected,
        "actual": actual,
        "operator": operator,
    }


def _runs_by_pr(report: dict[str, object]) -> dict[int, dict[str, object]]:
    runs = report.get("runs")
    if not isinstance(runs, list):
        return {}
    by_pr: dict[int, dict[str, object]] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        pr_number = _coerce_int(run.get("pr"))
        if pr_number > 0:
            by_pr[pr_number] = {str(key): value for key, value in run.items()}
    return by_pr


def _run_delta(
    pr_number: int,
    current: dict[str, object] | None,
    baseline: dict[str, object] | None,
) -> dict[str, object]:
    status = "changed"
    if current is None:
        status = "missing_current"
    elif baseline is None:
        status = "new"
    elif _int_run(current, "findings_count") == _int_run(baseline, "findings_count"):
        status = "unchanged"
    return {
        "pr": pr_number,
        "status": status,
        "findings_delta": _int_run(current or {}, "findings_count")
        - _int_run(baseline or {}, "findings_count"),
        "failures_delta": (1 if current and current.get("failure") else 0)
        - (1 if baseline and baseline.get("failure") else 0),
        "runtime_ms_delta": round(
            _float_run(current or {}, "runtime_ms") - _float_run(baseline or {}, "runtime_ms"),
            2,
        ),
    }


def _markdown_comparison(comparison: dict[str, object]) -> list[str]:
    totals = _object_dict(comparison.get("totals_delta"))
    lines = [
        "",
        "## Trend Comparison",
        "",
        f"- Baseline: `{comparison.get('baseline_path', '')}`",
        f"- Findings delta: {totals.get('findings', 0)}",
        f"- Failures delta: {totals.get('failures', 0)}",
        f"- Runtime delta ms: {totals.get('runtime_ms', 0)}",
    ]
    runs = comparison.get("runs")
    if isinstance(runs, list) and runs:
        lines.extend(
            [
                "",
                "| PR | Status | Findings Delta | Failures Delta | Runtime ms Delta |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for item in runs:
            if isinstance(item, dict):
                lines.append(
                    "| {pr} | {status} | {findings} | {failures} | {runtime} |".format(
                        pr=item.get("pr", ""),
                        status=item.get("status", ""),
                        findings=item.get("findings_delta", 0),
                        failures=item.get("failures_delta", 0),
                        runtime=item.get("runtime_ms_delta", 0),
                    )
                )
    return lines


def _markdown_assertions(assertions: dict[str, object]) -> list[str]:
    lines = [
        "",
        "## Expected Finding Assertions",
        "",
        f"- Expectations: `{assertions.get('expectations_path', '')}`",
        f"- Passed: {assertions.get('passed', 0)}",
        f"- Failed: {assertions.get('failed', 0)}",
        "",
        "| PR | Passed | Check | Expected | Actual |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    items = assertions.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            checks = item.get("checks")
            if not isinstance(checks, list):
                continue
            for check in checks:
                if not isinstance(check, dict):
                    continue
                lines.append(
                    "| {pr} | {passed} | {name} | {expected} | {actual} |".format(
                        pr=item.get("pr", ""),
                        passed=check.get("passed", False),
                        name=check.get("name", ""),
                        expected=check.get("expected", ""),
                        actual=check.get("actual", ""),
                    )
                )
    return lines


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
    return _coerce_float(value)


def _coerce_int(value: object) -> int:
    if isinstance(value, int):
        return value
    return 0


def _coerce_float(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        {str(key): item for key, item in value_item.items()}
        for value_item in value
        if isinstance(value_item, dict)
    ]
