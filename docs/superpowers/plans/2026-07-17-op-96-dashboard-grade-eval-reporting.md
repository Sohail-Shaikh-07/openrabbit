# OP-96 Dashboard-Grade Eval Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `openrabbit eval` with dashboard-ready JSON and Markdown reporting for regression runs, command outcomes, context-source metrics, tool-finding summaries, scenario groups, and trend data.

**Architecture:** Keep `src/cli/commands/eval.py` as the single reporting engine. Add structured, additive top-level report sections derived from the existing run records so the current schema remains usable, then render those same sections in Markdown. Add a small CLI option for named scenario groups without changing the default PR set.

**Tech Stack:** Python 3.12, Typer CLI, stdlib JSON and pathlib, existing OpenRabbit review summary dictionaries, pytest.

## Global Constraints

- Keep OpenRabbit local-first and do not add hosted dashboards, telemetry, network services, or new mandatory dependencies.
- Keep `schema_version` at `1` and make report changes additive to existing fields.
- Preserve the default eval PR set `1,2,3,4,5`.
- Do not write raw tool output, tokens, API keys, or credentials into eval JSON or Markdown.
- Markdown output must be generated from the same report dictionary written to JSON.
- Scenario group parsing must reject invalid PR numbers with the same positive-integer rule used by `parse_pr_numbers`.
- Failed PR runs must still produce complete report records and dashboard aggregates.

---

## File Structure

- Modify `src/cli/commands/eval.py`
  - Own scenario group parsing.
  - Add per-run `scenario_group`.
  - Add top-level `scenario_groups`, `dashboard`, `command_outcomes`, `context_sources`, and `tool_findings`.
  - Extend comparison output with dashboard trend series.
  - Render the new sections in Markdown.
- Modify `src/cli/main.py`
  - Add repeatable `--scenario-group NAME=1,2,3` option.
  - Pass parsed group specs into `run_eval_blocking`.
- Modify `tests/cli/test_eval_command.py`
  - Add parser tests, JSON report tests, Markdown dashboard tests, and failure aggregation tests.
- Modify `README.md`
  - Document the new dashboard-grade fields and `--scenario-group`.
- Create `docs/eval-reporting.md`
  - Document the JSON shape and dashboard interpretation.

---

### Task 1: Scenario Groups

**Files:**
- Modify: `src/cli/commands/eval.py`
- Modify: `src/cli/main.py`
- Test: `tests/cli/test_eval_command.py`

**Interfaces:**
- Produces: `parse_scenario_groups(values: list[str] | None, selected_prs: list[int]) -> dict[str, list[int]]`
- Produces: `run_eval(..., scenario_groups: dict[str, list[int]] | None = None, ...) -> dict[str, object]`
- Produces: `run_eval_blocking(..., scenario_groups: dict[str, list[int]] | None = None, ...) -> dict[str, object]`
- Consumes: existing `parse_pr_numbers(value: str) -> list[int]`

- [ ] **Step 1: Add failing parser tests**

Add these tests to `tests/cli/test_eval_command.py`:

```python
from cli.commands.eval import parse_pr_numbers, parse_scenario_groups, run_eval


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
```

- [ ] **Step 2: Run parser tests and verify they fail**

Run:

```powershell
python -m pytest tests/cli/test_eval_command.py::test_parse_scenario_groups_accepts_named_pr_sets tests/cli/test_eval_command.py::test_parse_scenario_groups_defaults_to_all_selected_prs tests/cli/test_eval_command.py::test_parse_scenario_groups_rejects_invalid_specs -q
```

Expected: failure because `parse_scenario_groups` does not exist.

- [ ] **Step 3: Implement scenario group parsing**

Add this helper near `parse_pr_numbers` in `src/cli/commands/eval.py`:

```python
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
```

- [ ] **Step 4: Add scenario group fields to eval records**

Update `run_eval` to accept `scenario_groups` and compute `groups = scenario_groups or parse_scenario_groups(None, pr_numbers)`. Add `scenario_group=_scenario_group_for_pr(number, groups)` when calling `_run_record_from_summary` and include `"scenario_group": scenario_group` in failure records.

Add:

```python
def _scenario_group_for_pr(pr_number: int, groups: dict[str, list[int]]) -> str:
    matches = [name for name, numbers in groups.items() if pr_number in numbers]
    return matches[0] if matches else "ungrouped"


def _scenario_group_records(groups: dict[str, list[int]]) -> list[dict[str, object]]:
    return [{"name": name, "prs": list(numbers)} for name, numbers in sorted(groups.items())]
```

Add top-level `"scenario_groups": _scenario_group_records(groups)` to the report.

- [ ] **Step 5: Wire CLI option**

In `src/cli/main.py`, add:

```python
scenario_group: list[str] | None = typer.Option(
    None,
    "--scenario-group",
    help="Named scenario group in NAME=1,2 format. Repeat for multiple groups.",
),
```

Then after `pr_numbers = parse_pr_numbers(prs)`, call:

```python
groups = parse_scenario_groups(scenario_group, pr_numbers)
```

Pass `scenario_groups=groups` into `run_eval_blocking`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
python -m pytest tests/cli/test_eval_command.py -q
```

Expected: all eval tests pass.

- [ ] **Step 7: Commit Task 1**

Run:

```powershell
git add src/cli/commands/eval.py src/cli/main.py tests/cli/test_eval_command.py
git commit -m "feat(op-96): add eval scenario groups"
```

---

### Task 2: Dashboard Summary Objects

**Files:**
- Modify: `src/cli/commands/eval.py`
- Test: `tests/cli/test_eval_command.py`

**Interfaces:**
- Produces: `_dashboard_summary(report: dict[str, object]) -> dict[str, object]`
- Produces: `_command_outcomes(runs: list[dict[str, object]]) -> dict[str, object]`
- Produces: `_context_sources(runs: list[dict[str, object]]) -> dict[str, object]`
- Produces: `_tool_findings(runs: list[dict[str, object]]) -> dict[str, object]`
- Produces: `_dashboard_trends(comparison: dict[str, object]) -> dict[str, object]`

- [ ] **Step 1: Add failing JSON tests**

Extend `test_run_eval_writes_json_and_markdown_reports` with assertions like:

```python
    assert data["dashboard"]["cards"]["prs"] == 2
    assert data["dashboard"]["charts"]["findings_by_pr"] == [
        {"pr": 1, "findings": 2, "scenario_group": "default"},
        {"pr": 2, "findings": 0, "scenario_group": "default"},
    ]
    assert data["command_outcomes"]["successes"] == 2
    assert data["context_sources"]["context_modes"] == {"loaded": 1, "diff only": 1}
    assert data["tool_findings"]["tools"]["ruff"]["diagnostics"] == 2
```

Extend the comparison test with:

```python
    assert data["dashboard"]["trend"]["totals_delta"]["findings"] == 1
    assert data["dashboard"]["trend"]["runs"][0]["pr"] == 1
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests/cli/test_eval_command.py::test_run_eval_writes_json_and_markdown_reports tests/cli/test_eval_command.py::test_run_eval_compares_baseline_and_checks_expectations -q
```

Expected: failures for missing dashboard fields.

- [ ] **Step 3: Implement command outcomes**

Add:

```python
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
```

- [ ] **Step 4: Implement context-source summary**

Add:

```python
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
```

- [ ] **Step 5: Implement tool-finding summary**

Add:

```python
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
```

- [ ] **Step 6: Implement dashboard summary**

Add:

```python
def _dashboard_summary(report: dict[str, object]) -> dict[str, object]:
    totals = _object_dict(report.get("totals"))
    runs = [run for run in report.get("runs", []) if isinstance(run, dict)]
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
            "context_modes": _context_sources(runs).get("context_modes", {}),
            "quality_statuses": totals.get("quality_status_counts", {}),
            "categories": totals.get("categories", {}),
        },
    }
    comparison = _object_dict(report.get("comparison"))
    if comparison:
        dashboard["trend"] = _dashboard_trends(comparison)
    return dashboard
```

Add:

```python
def _dashboard_trends(comparison: dict[str, object]) -> dict[str, object]:
    return {
        "totals_delta": _object_dict(comparison.get("totals_delta")),
        "runs": [
            {str(key): value for key, value in item.items()}
            for item in comparison.get("runs", [])
            if isinstance(item, dict)
        ],
    }
```

After optional comparison and assertions are added, set:

```python
    report["command_outcomes"] = _command_outcomes(runs)
    report["context_sources"] = _context_sources(runs)
    report["tool_findings"] = _tool_findings(runs)
    report["dashboard"] = _dashboard_summary(report)
```

- [ ] **Step 7: Run focused tests**

Run:

```powershell
python -m pytest tests/cli/test_eval_command.py -q
```

Expected: all eval tests pass.

- [ ] **Step 8: Commit Task 2**

Run:

```powershell
git add src/cli/commands/eval.py tests/cli/test_eval_command.py
git commit -m "feat(op-96): add eval dashboard summaries"
```

---

### Task 3: Markdown Dashboard And Documentation

**Files:**
- Modify: `src/cli/commands/eval.py`
- Modify: `README.md`
- Create: `docs/eval-reporting.md`
- Test: `tests/cli/test_eval_command.py`
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: report fields from Task 2.
- Produces: Markdown sections `## Dashboard Summary`, `## Scenario Groups`, `## Context Sources`, `## Tool Findings`, and existing comparison/assertion sections.

- [ ] **Step 1: Add Markdown assertions**

In `test_run_eval_writes_json_and_markdown_reports`, assert:

```python
    markdown_text = markdown.read_text(encoding="utf-8")
    assert "## Dashboard Summary" in markdown_text
    assert "## Scenario Groups" in markdown_text
    assert "## Context Sources" in markdown_text
    assert "## Tool Findings" in markdown_text
```

- [ ] **Step 2: Run Markdown test and verify it fails**

Run:

```powershell
python -m pytest tests/cli/test_eval_command.py::test_run_eval_writes_json_and_markdown_reports -q
```

Expected: failure for missing Markdown sections.

- [ ] **Step 3: Render dashboard sections**

In `_markdown_report`, after the report metadata lines and before the per-run table, insert helper output from:

```python
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
            if isinstance(group, dict):
                lines.append(f"- {group.get('name')}: {', '.join(str(pr) for pr in group.get('prs', []))}")
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
                f"- {tool}: diagnostics={item.get('diagnostics', 0)}, statuses={_category_text(item.get('statuses'))}"
            )
    else:
        lines.append("- No local quality tool findings recorded.")
    lines.append("")
    return lines
```

Call it with:

```python
        "",
        *_markdown_dashboard_sections(report),
        "| PR | Context | Memory | Quality | Diagnostics | Learnings | Guidelines | Linked Issues | Findings | Categories | Dropped | Skipped | Runtime ms | Failure |",
```

- [ ] **Step 4: Add docs**

Create `docs/eval-reporting.md` with:

```markdown
# Eval Reporting

`openrabbit eval` writes a local JSON report and optional Markdown dashboard for repeatable PR review regression runs.

The report is additive and keeps `schema_version` at `1`. Existing consumers can continue to read `runs` and `totals`; dashboard consumers should prefer the derived `dashboard`, `command_outcomes`, `context_sources`, and `tool_findings` sections.

## Scenario Groups

Use repeatable `--scenario-group NAME=1,2` options to label selected PRs.

```bash
openrabbit eval --repo owner/repo --prs 1,2,3 --scenario-group security=1 --scenario-group quality=2,3
```

Each run receives a `scenario_group` field. The top-level `scenario_groups` array lists the configured groups.

## Dashboard Fields

- `dashboard.cards`: headline totals for PRs, findings, failures, dropped findings, quality diagnostics, and runtime.
- `dashboard.charts.findings_by_pr`: PR-level finding counts grouped by scenario.
- `dashboard.charts.runtime_by_pr`: PR-level runtime values grouped by scenario.
- `dashboard.charts.context_modes`: loaded versus diff-only context counts.
- `dashboard.charts.quality_statuses`: local quality gate status counts.
- `command_outcomes`: success and failure counts with failed command details.
- `context_sources`: context, memory, guideline, learning, and linked issue totals.
- `tool_findings`: local quality diagnostics grouped by tool.

When `--compare` is used, `dashboard.trend` mirrors the comparison deltas in chart-friendly form.

## Privacy

Eval reports do not include raw tool output, tokens, API keys, or credentials. Local quality gates contribute normalized diagnostics only.
```

- [ ] **Step 5: Update README**

In the `openrabbit eval` section, add:

```markdown
openrabbit eval --repo owner/repo --scenario-group security=1,4 --scenario-group quality=2,3
```

Then add one paragraph:

```markdown
The JSON report also includes dashboard-ready `dashboard`, `command_outcomes`, `context_sources`, `tool_findings`, and `scenario_groups` sections. These are derived from the same local run records and can be used to build charts without sending code or results to a hosted service. See [docs/eval-reporting.md](docs/eval-reporting.md).
```

- [ ] **Step 6: Add docs test**

If `tests/test_docs.py` has link checks, add `docs/eval-reporting.md` to the expected docs list. If it only checks specific phrases, add an assertion that README references `docs/eval-reporting.md`.

- [ ] **Step 7: Run docs and eval tests**

Run:

```powershell
python -m pytest tests/cli/test_eval_command.py tests/test_docs.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 3**

Run:

```powershell
git add src/cli/commands/eval.py README.md docs/eval-reporting.md tests/cli/test_eval_command.py tests/test_docs.py
git commit -m "docs(op-96): document dashboard eval reporting"
```

---

### Task 4: Final Verification And PR

**Files:**
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: commits from Tasks 1 through 3.
- Produces: clean PR for issue #189.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests/cli/test_eval_command.py tests/test_docs.py -q
python -m ruff check src/cli/commands/eval.py src/cli/main.py tests/cli/test_eval_command.py tests/test_docs.py
python -m black --check src/cli/commands/eval.py src/cli/main.py tests/cli/test_eval_command.py tests/test_docs.py
python -m mypy
git diff --check
```

Expected: all commands pass.

- [ ] **Step 2: Request final code review**

Create a review package from the branch base to HEAD and dispatch a reviewer. The reviewer must check:

- Scenario group parsing and CLI behavior.
- Dashboard fields are derived from sanitized existing data.
- Failure records still aggregate correctly.
- Markdown does not diverge from JSON.
- No new external services or dependencies.

- [ ] **Step 3: Run full verification**

Run:

```powershell
python -m pytest
python -m ruff check $(git ls-files '*.py')
python -m black --check .
python -m mypy
python scripts/smoke_test.py
poetry build
```

Expected: all commands pass.

- [ ] **Step 4: Push and create PR**

Run:

```powershell
git push -u origin feature/op-96-dashboard-grade-eval-reporting
gh pr create --title "[OP-96] Add dashboard-grade eval reporting" --body "<project PR template body>"
```

PR body must include:

```text
Summary

Adds dashboard-grade local eval reporting for scenario groups, command outcomes, context-source metrics, tool findings, and chart-ready trend data.

What was fixed

* Added named eval scenario groups.
* Added dashboard-ready JSON sections derived from local eval runs.
* Added Markdown dashboard sections and docs.
* Preserved local-first behavior and backward-compatible report fields.

Testing

* python -m pytest
* python -m ruff check <tracked Python files>
* python -m black --check .
* python -m mypy
* python scripts/smoke_test.py
* poetry build

Closes #189
```

- [ ] **Step 5: Finish the loop**

Wait for GitHub CI, merge only if green, update Notion OP-96 to Done, and sync local `main`.

