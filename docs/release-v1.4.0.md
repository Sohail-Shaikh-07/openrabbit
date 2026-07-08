# OpenRabbit v1.4.0 Release Notes

OpenRabbit v1.4.0 focuses on context intelligence and automation. The release makes repository context more explainable, improves unattended review controls, adds provider health checks, expands PR exploration output formats, and turns `openrabbit eval` into a stronger regression evidence tool.

## Highlights

- Improved RAG context selection with changed-file, changed-symbol, directory, guideline, and semantic retrieval reasons.
- Added review daemon controls for concurrency, cooldowns, changed-file skips, and structured observability logs.
- Hardened the GitHub Actions recipe for self-hosted runners with dry-run defaults and Qdrant health checks.
- Added `openrabbit model-health` for Ollama, OpenAI, and OpenAI-compatible provider diagnostics.
- Added `--format text|markdown|json` to `openrabbit describe` and `openrabbit ask`.
- Added `openrabbit eval --compare` for historical trend deltas.
- Added `openrabbit eval --expectations` for expected finding assertions.

## Upgrade Notes

- Package version is `1.4.0`.
- Python support remains `>=3.12,<3.14`.
- The default provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- Qdrant remains optional. Reviews continue in diff-only mode when no index is available.
- Run `openrabbit model-health --workspace .` after changing provider config.
- Run `openrabbit index --workspace . --health` before relying on RAG context in automation.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m mypy`
- `python -m ruff check $(git ls-files '*.py')`
- `python -m black --check .`
- `python scripts/smoke_test.py`
- `poetry build`

The release workflow also checks that a `v1.4.0` tag matches the package version before publishing artifacts.

## Deferred Work

The following CodeRabbit-parity items remain planned for later phases:

- Graph and vector memory plugins.
- MCP and web-search knowledge sources.
- Jira and Linear linked work item context.
- SAST and linter integrations.
- Dashboards and hosted quality analytics.
- Autofix workflows.
