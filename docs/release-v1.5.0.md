# OpenRabbit v1.5.0 Release Notes

OpenRabbit v1.5.0 focuses on CodeRabbit-parity foundations while preserving the local-first default. The release improves PR memory, polling commands, managed summaries, local quality evidence, path and AST-scoped review controls, eval dashboards, and future knowledge connector boundaries.

## Highlights

- Added sanitized PR conversation history for `review`, `describe`, `ask`, and `improve`.
- Added stale finding transitions and clearer memory status summaries.
- Added polling commands for `/openrabbit ignore`, `/openrabbit summary`, and `/openrabbit configuration`.
- Added `openrabbit describe --publish` for one managed OpenRabbit PR walkthrough comment.
- Added optional local quality gates for Ruff, mypy, pytest, Bandit, Semgrep, ESLint, and npm test.
- Added AST-scoped review instructions for Python, JavaScript, and TypeScript symbols.
- Added dashboard-ready eval JSON and Markdown report sections.
- Added dependency-free optional knowledge connector contracts for future MCP, web search, multi-repo, Jira, Linear, and document context.

## Upgrade Notes

- Package version is `1.5.0`.
- Python support remains `>=3.12,<3.14`.
- The default provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- Local quality gates are disabled by default. Enable them with `quality.enabled: true`.
- AST review controls are opt-in through `review.ast_instructions`.
- Optional knowledge connectors are design-time extension points only in this release. They do not run during reviews and add no required services.
- Qdrant remains optional. Reviews continue in diff-only mode when no index is available.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m mypy`
- `python -m ruff check $(git ls-files '*.py')`
- `python -m black --check .`
- `python scripts/smoke_test.py`
- `poetry build`

The release workflow also checks that a `v1.5.0` tag matches the package version before publishing artifacts.

## Deferred Work

The following CodeRabbit-parity items remain planned for later phases:

- Runtime MCP, web-search, Jira, Linear, and multi-repo knowledge connectors.
- Graph and vector memory plugins.
- Webhook/server deployment mode.
- Line-level ask workflows.
- Repository maintenance commands for labels, changelogs, docs generation, and similar issue lookup.
- SAST dashboards, hosted quality analytics, and autofix workflows.
