# OpenRabbit v1.8 Interactive PR Workflow Plan

OpenRabbit v1.8 focuses on making PR interaction commands more scriptable and more useful inside pull request conversations. The release should tighten `describe`, `ask`, and `improve` outputs, add explicit managed publishing paths, and keep every mutating action opt-in, bounded, and easy to audit.

## Goals

- Add stable JSON output contracts for `describe`, `ask`, and `improve`.
- Keep local CLI output useful for humans while making command results easy to consume from automation.
- Add explicitly managed PR publishing for summary and answer workflows without changing read-only defaults.
- Support focused line-level ask context for changed files and changed new-side lines.
- Measure improvement suggestion quality so helpful suggestions do not become noisy advice.
- Preserve local-first privacy boundaries, source labels, and deterministic grounding.

## Planned Work

| Task | Focus | Outcome |
| --- | --- | --- |
| OP-122 | v1.8 planning | Release plan, task sequence, and repository roadmap links |
| OP-123 | JSON output contracts | Stable schemas for `describe`, `ask`, and `improve` scripting |
| OP-124 | Managed describe publishing | One explicitly managed PR summary or walkthrough comment |
| OP-125 | Managed ask publishing | One explicitly managed answer comment when requested |
| OP-126 | Line-level ask context | Focused answers for a selected changed file and line |
| OP-127 | Improve quality evaluation | Regression coverage for over-suggestion and false-positive behavior |
| OP-128 | Label proposal output | Read-only label suggestions based on PR metadata and review context |
| OP-129 | Docs and examples | User-facing guidance for interactive workflow commands |
| OP-130 | Security and regression tests | Publishing boundaries, schemas, context bounds, and privacy checks |
| OP-131 | v1.8.0 release | Version bump, changelog, release notes, CI, tag, and release artifacts |

## Progress Notes

- OP-122 creates the v1.8 planning track and repository roadmap link.
- OP-123 adds top-level `schema_version` and `command` fields to `describe`, `ask`, and `improve` JSON payloads, plus `openrabbit improve --format json` for scripting.
- OP-124 adds explicit `managed_summary` metadata to `describe` output so read-only and `--publish` runs expose the managed marker, status, comment ID, and comment URL.
- OP-125 adds `ask --publish` for one managed PR answer comment plus `managed_answer` metadata for read-only and published runs.
- OP-126 adds `ask --file ... --line ...` for focused changed-line questions with nearby diff context and `ask_focus` metadata.
- OP-127 adds `suggestion_quality` metadata to `improve` output and regression coverage for ungrounded, vague, comment-only, and unsafe dependency suggestions.
- OP-128 adds read-only `labels` output for PR label proposals with reasons, confidence, repository-label availability, and stable JSON.
- OP-129 adds the interactive PR workflow guide and a manual GitHub Actions example for describe, ask, improve, and labels usage.

## Scope Notes

The v1.8 work should make command behavior easier to automate without turning OpenRabbit into an always-mutating bot. Every publishing path should remain explicit, use a recognizable OpenRabbit-managed marker, and be safe to rerun without creating duplicate comments.

`describe` should continue to be useful as a local summary command. Managed publishing should update one summary comment rather than rewrite the PR body by default. `ask` should stay read-only unless a publish flag or PR command explicitly requests a managed answer. `improve` should keep suggestions grounded to changed files and changed new-side lines, with evaluation coverage for noisy or weak suggestions.

Label proposals are intentionally read-only in this release. They can prepare the surface for future repo-maintenance commands without mutating repository labels or requiring new GitHub permissions.

## Non Goals

- No automatic PR body rewrites by default.
- No automatic label mutation.
- No arbitrary patch application or branch pushes.
- No hosted service requirement.
- No expansion of connector write behavior beyond explicitly managed comments.
- No webhook or GitHub App server mode in this release.

## Validation Plan

- Keep full local gates: `python -m ruff check src tests`, `python -m black --check src tests`, `python -m mypy src`, `python -m pytest`, `python -m build`, and CLI smoke checks.
- Add focused unit tests for JSON schemas, publish markers, idempotent managed-comment updates, line-level ask bounds, and suggestion quality filters.
- Use `testing-openrabbit` for external smoke checks of installed commands and managed publishing behavior against test PRs.
- Keep release readiness tied to green CI, local main sync, local reinstall, and a successful v1.8.0 release workflow.
