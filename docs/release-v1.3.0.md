# OpenRabbit v1.3.0 Release Notes

OpenRabbit v1.3.0 focuses on memory and knowledge. The release makes local PR memory easier to inspect and maintain, then adds the first durable context sources that help OpenRabbit understand repository rules and issue intent beyond the raw diff.

## Highlights

- Added deterministic memory export and date-based pruning.
- Added explicit local repository learnings through `@openrabbit learn ...`.
- Added memory backend design docs for future graph and vector plugins while keeping SQLite as the only required backend.
- Added automatic repository guideline detection for common agent and editor instruction files.
- Added path-local guideline scope metadata, prompt labels, and context provenance.
- Added linked GitHub issue context from PR text, branch metadata, and commit messages.
- Extended `openrabbit eval` with memory context, active learning count, guideline sources, and linked issue count.

## Upgrade Notes

- Package version is `1.3.0`.
- Python support remains `>=3.12,<3.14`.
- The default provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- Local memory remains enabled by default and stored under `.openrabbit/state/openrabbit.db` unless configured otherwise.
- Explicit learnings are enabled by default through `memory.learnings_enabled: true`.
- Repository guideline detection requires a fresh `openrabbit index` run before new guideline files appear in RAG context.
- Linked issue context is best-effort. If an issue cannot be fetched, the review continues without that issue context.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m mypy`
- `python -m ruff check $(git ls-files '*.py')`
- `python -m black --check .`
- `python scripts/smoke_test.py`
- `poetry build`

The release workflow also checks that a `v1.3.0` tag matches the package version before publishing artifacts.

## Deferred Work

The following CodeRabbit-parity items remain planned for later phases:

- Graph and vector memory plugins.
- MCP and web-search knowledge sources.
- Jira and Linear linked work item context.
- SAST and linter integrations.
- Dashboards and historical quality trends.
- Autofix workflows.
