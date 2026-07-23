# OpenRabbit v1.7 Context Precision Plan

OpenRabbit v1.7 focuses on making review context more precise before it reaches model prompts. The release should improve how RAG, local memory, linked issues, local quality gates, and optional connector snippets are selected, packed, measured, and explained while preserving local-first defaults.

## Goals

- Prefer directly relevant changed files, symbols, tests, scoped guidelines, and architecture docs over broad semantic matches.
- Balance repository RAG and connector snippets under explicit source budgets.
- Keep changed-line evidence first in every review prompt.
- Preserve connector privacy boundaries: disabled by default, redacted, source-labeled, bounded, and fail-open.
- Make context selection measurable through eval reports and troubleshooting output.

## Planned Work

| Task | Focus | Outcome |
| --- | --- | --- |
| OP-113 | Context precision telemetry | Structured diagnostics for selected sources, dropped sources, scores, and prompt budget use |
| OP-114 | Changed-symbol RAG retrieval | More precise changed-symbol, nearby-code, tests, guideline, and architecture retrieval |
| OP-115 | Unified context packing | Shared source budgets across diff evidence, RAG, memory, linked issues, quality gates, and connectors |
| OP-116 | Connector relevance scoring | Deterministic connector filtering using PR metadata, paths, symbols, issue keys, and source kind |
| OP-117 | Large PR summarization | Bounded summaries or deprioritization for low-risk oversized file changes |
| OP-118 | Context precision eval | Repeatable scenarios and report fields for context relevance and prompt budget use |
| OP-119 | Docs and troubleshooting | User-facing guidance for retrieval reasons, source budgets, connector relevance, and noisy or missing context |
| OP-120 | Security and privacy regressions | Redaction, skipped-path, bounds, source trust, and fail-open coverage for RAG and connector context |
| OP-121 | v1.7.0 release | Version bump, changelog, release notes, CI, tag, and release artifacts |

## Progress Notes

- OP-113 adds the first context precision telemetry surface. Model-facing command JSON now includes `context_diagnostics` with candidate counts, selected sources, dropped reasons, score summaries, connector availability counts, and estimated prompt-packing size. Eval reports aggregate those fields into totals, dashboard cards, context-source summaries, and Markdown output.
- OP-114 improves deterministic RAG planning for changed symbols, related tests, scoped guidelines, nearby code, and architecture docs so direct repository evidence is selected before broad semantic context.
- OP-115 adds shared source budgets for changed-line evidence, compressed diff evidence, repository RAG, connector snippets, PR memory, linked GitHub issues, and local quality diagnostics so noisy auxiliary sources cannot crowd out higher-priority review evidence.
- OP-116 adds deterministic connector relevance scoring and filtering with signals from linked issue keys, changed paths, changed symbols, repository handles, source kind, provider scores, and text overlap.
- OP-117 adds visibly marked compressed-diff summaries for oversized low-risk files and diagnostics for summarized low-risk file counts, changes, and diff lines.

## Non Goals

- No mandatory hosted services.
- No automatic repository cloning.
- No connector write expansion beyond explicitly managed issue-tracker comments.
- No broad model-provider rewrite.
- No hosted dashboard requirement.

## Validation Plan

- Keep full local gates: `python -m ruff check src tests`, `python -m black --check src tests`, `python -m mypy src`, `python -m pytest`, `python -m build`, and CLI smoke checks.
- Add focused tests for retrieval diagnostics, context packing, connector filtering, and eval report fields as each task lands.
- Use `testing-openrabbit` for external smoke artifacts and real-world context precision scenarios.
- Keep release readiness tied to green CI, local main sync, local reinstall, and successful v1.7.0 release workflow.
