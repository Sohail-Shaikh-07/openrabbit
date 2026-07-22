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

- `dashboard.cards`: headline totals for PRs, findings, failures, dropped findings, selected and dropped context items, quality diagnostics, and runtime.
- `dashboard.charts.findings_by_pr`: PR-level finding counts grouped by scenario.
- `dashboard.charts.runtime_by_pr`: PR-level runtime values grouped by scenario.
- `dashboard.charts.context_modes`: loaded versus diff-only context counts.
- `dashboard.charts.context_dropped_reasons`: RAG and connector context drop reasons such as top-k limits or connector item caps.
- `dashboard.charts.quality_statuses`: local quality gate status counts.
- `command_outcomes`: success and failure counts with failed command details.
- `context_sources`: context, memory, guideline, learning, linked issue, connector, context candidate, context selected, context dropped, and prompt-packing totals.
- `tool_findings`: local quality diagnostics grouped by tool.

When `--compare` is used, `dashboard.trend` mirrors the comparison deltas in chart-friendly form.

## Privacy

Eval reports do not include raw tool output, raw prompt text, tokens, API keys, or credentials. Local quality gates contribute normalized diagnostics only. Context precision diagnostics expose counts, source labels, drop reasons, scores, and estimated prompt-packing size without embedding unbounded source or connector bodies.
