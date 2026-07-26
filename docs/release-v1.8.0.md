# OpenRabbit v1.8.0 Release Notes

OpenRabbit v1.8.0 focuses on Interactive PR Workflows while preserving local-first defaults. The release makes `describe`, `ask`, `improve`, and `labels` easier to run from terminals, scripts, PR comments, and manual GitHub Actions workflows while keeping every GitHub write explicit and bounded.

## Highlights

- Added stable JSON contract markers for `describe`, `ask`, and `improve`, and added `openrabbit improve --format json`.
- Added explicit `managed_summary` metadata to `openrabbit describe` output so `--publish` runs report the managed marker, created/updated status, comment ID, and comment URL.
- Added opt-in managed answer publishing for `openrabbit ask --publish` with stable `managed_answer` metadata and idempotent marker comment updates.
- Added line-focused `openrabbit ask --file ... --line ...` context with changed-line validation and `ask_focus` metadata.
- Added `suggestion_quality` metadata to `openrabbit improve` output with raw, grounded, kept, dropped, and drop-reason counts for noisy or unsafe suggestions.
- Added read-only `openrabbit labels` output for PR label proposals with reasons, confidence, repository-label availability, and stable JSON.
- Added interactive PR workflow documentation and a manual GitHub Actions example for `describe`, `ask`, `improve`, and `labels`.
- Added release-blocking regressions for interactive JSON contracts, managed comment markers, read-only label boundaries, and privacy-safe label proposal summaries.

## Upgrade Notes

- Package version is `1.8.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- Qdrant remains optional. Reviews and interactive commands continue in diff-only mode when no index is available.
- `/openrabbit ...` remains the preferred PR command prefix, with legacy mention-trigger compatibility retained.
- `openrabbit describe` and `openrabbit ask` stay read-only unless `--publish` is provided.
- `openrabbit labels` is intentionally read-only in this release and has no `--publish` mode.
- Managed PR comments use stable OpenRabbit markers and update in place instead of creating duplicate comments.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m mypy src`
- `python -m ruff check src tests`
- `python -m black --check src tests`
- `python scripts/smoke_test.py`
- `python -m build`

The release workflow also checks that a `v1.8.0` tag matches the package version before publishing artifacts.

## Deferred Work

The following CodeRabbit-parity items remain planned for later phases:

- Graph and vector memory plugins.
- Webhook/server deployment mode.
- Repository maintenance commands that mutate labels, changelogs, or docs.
- SAST dashboards, hosted quality analytics, and autofix workflows.
- Team collaboration workflows beyond local-first CLI and GitHub integration.
