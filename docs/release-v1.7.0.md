# OpenRabbit v1.7.0 Release Notes

OpenRabbit v1.7.0 focuses on Context Precision while preserving the local-first default. Repository RAG, linked issues, local quality diagnostics, PR memory, compressed diffs, and optional connector snippets are now selected, packed, measured, and explained with clearer source budgets and privacy boundaries.

## Highlights

- Added structured context precision diagnostics for model-facing commands, including candidate counts, selected sources, selected reasons, dropped reasons, score summaries, connector availability, source budgets, and prompt-packing estimates.
- Improved repository RAG planning so changed files, changed symbols, related tests, nearby paths, scoped guideline files, and architecture docs are preferred before broad semantic context.
- Added shared prompt source budgets for changed-line evidence, compressed diff evidence, repository RAG, connector snippets, PR memory, linked GitHub issues, and local quality diagnostics.
- Added deterministic connector relevance scoring for linked issue keys, changed paths, changed symbols, repository handles, source kind, provider score, and PR text overlap.
- Added visible summaries for oversized low-risk files so generated docs, lock-style files, and manifests can stay bounded while risky code diffs remain visible.
- Expanded `openrabbit eval` with context precision fields and dashboard summaries for selected sources, selected reasons, RAG and connector contribution, relevance scores, source-budget usage, budget overages, prompt tokens, and large low-risk summaries.
- Added a packaged v1.7 context precision benchmark corpus for changed-symbol context, linked connector evidence, and large low-risk summary scenarios.
- Added `docs/context-precision.md` for retrieval reasons, source budgets, connector relevance, eval interpretation, missing context, noisy context, budget pressure, and privacy boundaries.
- Added privacy and security regressions for connector metadata redaction, redacted fail-open errors, source-budget isolation, skipped-path context behavior, prompt bounds, and diagnostics without raw context bodies.

## Upgrade Notes

- Package version is `1.7.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- Qdrant remains optional. Reviews continue in diff-only mode when no index is available.
- Optional connectors remain disabled by default and must be enabled explicitly.
- Connector snippets remain bounded, redacted, source-labeled, untrusted, and fail open.
- Eval reports remain local artifacts and do not include raw prompt text, raw tool output, API keys, credentials, or unbounded connector bodies.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m mypy src`
- `python -m ruff check src tests`
- `python -m black --check src tests`
- `python scripts/smoke_test.py`
- `python -m build`

The release workflow also checks that a `v1.7.0` tag matches the package version before publishing artifacts.

## Deferred Work

The following CodeRabbit-parity items remain planned for later phases:

- Graph and vector memory plugins.
- Webhook/server deployment mode.
- Line-level ask workflows.
- Repository maintenance commands for labels, changelogs, docs generation, and similar issue lookup.
- SAST dashboards, hosted quality analytics, and autofix workflows.
