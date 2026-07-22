# OpenRabbit v1.6.0 Release Notes

OpenRabbit v1.6.0 focuses on Connector Intelligence while preserving the local-first default. Optional MCP, web search, Jira, Linear, and multi-repo context can now enrich model-facing commands when explicitly configured, and reviews still run without mandatory external services.

## Highlights

- Added `openrabbit connector-health` for disabled-by-default MCP, web search, multi-repo, Jira, and Linear readiness checks.
- Added an optional MCP client runtime for stdio and Streamable HTTP servers with approved tool/resource allowlists.
- Added MCP-backed web search through configured MCP servers instead of direct vendor SDK clients.
- Added Jira and Linear linked issue reads plus opt-in managed issue-tracker summary comments.
- Added explicit multi-repo local context loading for configured sibling repositories without auto-cloning.
- Wired connector snippets into `review`, `describe`, `ask`, `improve`, and `eval` reports with untrusted-source labels, redaction, bounds, provenance, and deduplication.
- Added connector security and regression coverage for redaction, fail-open behavior, auth/rate-limit failures, malformed responses, sanitized write failures, and duplicate managed-comment prevention.
- Added connector setup, permission boundary, generated config, and troubleshooting documentation.
- Added daemon lifecycle polish with `openrabbit start --once`, daemon metadata, stale cleanup, and `openrabbit stop --workspace ...`.
- Switched user-facing PR comment examples to `/openrabbit ...` while retaining legacy mention-trigger compatibility.

## Upgrade Notes

- Package version is `1.6.0`.
- Python support remains `>=3.12,<3.14`.
- The default provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- Optional connectors remain disabled by default. Enable only the connector blocks the repository needs.
- Connector token values must live in environment variables such as `JIRA_API_TOKEN` or `LINEAR_API_KEY`; do not store secret values in config files.
- Install optional MCP dependencies with `poetry install --with connectors` before enabling MCP or MCP-backed web search.
- Jira and Linear write modes stay off by default. When enabled, each connector can only create or update one managed OpenRabbit summary comment on the linked issue.
- Qdrant remains optional. Reviews continue in diff-only mode when no index is available.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m mypy src`
- `python -m ruff check src tests`
- `python -m black --check src tests`
- `python scripts/smoke_test.py`
- `python -m build`

The release workflow also checks that a `v1.6.0` tag matches the package version before publishing artifacts.

## Deferred Work

The following CodeRabbit-parity items remain planned for later phases:

- Graph and vector memory plugins.
- Webhook/server deployment mode.
- Line-level ask workflows.
- Repository maintenance commands for labels, changelogs, docs generation, and similar issue lookup.
- SAST dashboards, hosted quality analytics, and autofix workflows.
