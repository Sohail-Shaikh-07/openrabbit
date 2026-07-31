# OpenRabbit v1.9.0 Release Notes

OpenRabbit v1.9.0 focuses on Repository Maintenance Automation while preserving local-first defaults. The release adds practical commands for maintainers who want safer label triage, release-note drafting, documentation follow-up, and related issue discovery without turning OpenRabbit into an always-mutating bot.

## Highlights

- Added `openrabbit labels --apply` for opt-in PR label application. The default remains read-only, repository labels are never created, and JSON output reports applied, skipped, and failed labels.
- Added `openrabbit changelog` for read-only release changelog drafts from merged PRs, labels, and bounded local release-note context.
- Added `openrabbit docs` for read-only documentation update suggestions from changed public surfaces, source paths, and added symbols.
- Added `openrabbit similar-issues` for read-only related issue lookup from linked issues, GitHub issue search, PR metadata, changed paths, labels, and local review memory signals.
- Added shared `workflow_controls` metadata for repository maintenance commands so dry-run, write intent, required permissions, file writes, and managed-comment support are auditable.
- Added maintenance automation security and regression coverage for label mutation safety, changelog and documentation bounds, similar issue privacy, permission failures, and shared workflow controls.
- Added repository maintenance automation documentation and a manual GitHub Actions workflow for labels, changelog, docs, and similar issue commands.

## Upgrade Notes

- Package version is `1.9.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- Qdrant remains optional. Review and maintenance commands continue without repository indexing when no index is available.
- Maintenance commands do not call a model.
- `openrabbit labels`, `openrabbit changelog`, `openrabbit docs`, and `openrabbit similar-issues` are read-only by default.
- `openrabbit labels --apply` is the only maintenance write mode in this release. It only applies labels that already exist in the repository.
- Changelog, docs, and similar issue commands do not mutate files or GitHub in this release.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m mypy src`
- `python -m ruff check src tests`
- `python -m black --check src tests`
- `python scripts/smoke_test.py`
- `python -m build`

The release workflow also checks that a `v1.9.0` tag matches the package version before publishing artifacts.

## Deferred Work

The following CodeRabbit-parity items remain planned for later phases:

- Automatic changelog or documentation file writes behind explicit apply paths.
- Managed maintenance summary comments beyond existing describe and ask publishing.
- Webhook/server deployment mode.
- SAST dashboards, hosted quality analytics, and autofix workflows.
- Team collaboration workflows beyond local-first CLI and GitHub integration.
