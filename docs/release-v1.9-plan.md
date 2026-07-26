# OpenRabbit v1.9 Repository Maintenance Automation Plan

OpenRabbit v1.9 focuses on repository maintenance automation after the v1.8 interactive workflow release. The release should add useful maintenance commands for labels, changelog drafts, documentation update suggestions, and similar issue lookup while preserving the local-first default and keeping every mutating action explicit, permission-aware, and auditable.

## Goals

- Build on read-only label proposals with an opt-in label mutation path.
- Add deterministic changelog draft output for release PRs without mutating files by default.
- Suggest documentation updates for public surfaces touched by a PR.
- Add read-only similar issue lookup using GitHub issue search, linked issues, and local memory.
- Share dry-run, publish, permission, and managed-comment controls across maintenance commands.
- Preserve privacy boundaries, bounded context, stable JSON, and clear source labels.

## Planned Work

| Task | Focus | Outcome |
| --- | --- | --- |
| OP-132 | v1.9 planning | Release plan, task sequence, and repository roadmap links |
| OP-133 | Opt-in label mutation | Explicit label apply or update command with dry-run defaults and audit output |
| OP-134 | Changelog assistant | Deterministic changelog draft output from merged PRs, labels, and release notes |
| OP-135 | Documentation suggestions | Suggested README, docs, and example updates for changed public surfaces |
| OP-136 | Similar issue lookup | Read-only similar issue results with source labels and bounded matching context |
| OP-137 | Maintenance workflow controls | Shared dry-run, publish, permission, and managed-comment behavior |
| OP-138 | Security and regression tests | Regression coverage for mutation safety, privacy, bounds, and permission failures |
| OP-139 | Docs and examples | User-facing maintenance command guide, GitHub Actions examples, and troubleshooting |
| OP-140 | v1.9.0 release | Version bump, changelog, release notes, CI, tag, and release artifacts |

## Progress Notes

- OP-132 creates the v1.9 planning track, seeds the Notion task sequence, and links the repository roadmap to this plan.

## Scope Notes

Maintenance commands should start as local CLI and manual automation primitives rather than an always-mutating bot. `labels` can gain an explicit write mode only when a user asks for it through a CLI flag or controlled workflow. Changelog and documentation commands should generate bounded drafts first; file mutation can come later only with a clear apply path, tests, and reviewable output.

Similar issue lookup should remain read-only in this release. It can use GitHub issue search, linked issue context, PR metadata, and local memory, but it must avoid sending unbounded repository content or raw secrets to external services.

## Non Goals

- No automatic label mutation by default.
- No automatic changelog or documentation file writes by default.
- No arbitrary patch application or branch pushes.
- No hosted dashboard requirement.
- No webhook or GitHub App server mode in this release.
- No broad provider or model-serving rewrite.

## Validation Plan

- Keep full local gates: `python -m ruff check src tests`, `python -m black --check src tests`, `python -m mypy src`, `python -m pytest`, `python -m build`, and CLI smoke checks.
- Add focused tests for label mutation permissions, dry-run defaults, changelog/doc suggestion bounds, similar issue privacy, and stable JSON fields.
- Use `testing-openrabbit` for external smoke checks of read-only and opt-in maintenance workflows.
- Keep release readiness tied to green CI, local main sync, local reinstall, and a successful v1.9.0 release workflow.
