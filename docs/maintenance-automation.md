# Repository Maintenance Automation

OpenRabbit v1.9 adds maintenance commands for pull request triage, release notes,
documentation follow-up, and related issue discovery. These commands are built
for local-first workflows and manual automation: read-only by default, explicit
about writes, and useful in terminal output or GitHub Actions logs.

## Command Matrix

| Command | Default behavior | Optional write | Required permissions |
| --- | --- | --- | --- |
| `openrabbit labels` | Proposes labels for one pull request | `--apply` adds proposed labels that already exist in the repository | `pull_requests:read`, `issues:read`; add `issues:write` for `--apply` |
| `openrabbit changelog` | Drafts release notes from merged pull requests and bounded local notes | None in v1.9 | `pull_requests:read` |
| `openrabbit docs` | Suggests README, docs, and example updates for one pull request | None in v1.9 | `pull_requests:read` |
| `openrabbit similar-issues` | Finds related issues from linked issues, GitHub issue search, PR metadata, and local memory | None in v1.9 | `pull_requests:read`, `issues:read` |

`labels --apply` is the only maintenance write mode in v1.9. It never creates
repository labels. Labels that do not already exist in the repository are
reported as skipped.

## Safe Local Recipes

Start with JSON output when you are wiring automation:

```bash
openrabbit labels --pr 42 --repo owner/repo --format json
openrabbit docs --pr 42 --repo owner/repo --format json
openrabbit similar-issues --pr 42 --repo owner/repo --format json
openrabbit changelog --repo owner/repo --since 2026-07-01 --until 2026-07-31 --format json
```

Apply labels only after reviewing the proposed labels:

```bash
openrabbit labels --pr 42 --repo owner/repo --apply --format json
```

Include local release-note context without allowing unbounded file output:

```bash
openrabbit changelog --repo owner/repo --notes docs/release-v1.9-plan.md --format json
```

Limit noisy outputs when a repository has a large issue or pull request history:

```bash
openrabbit labels --pr 42 --repo owner/repo --limit 5
openrabbit changelog --repo owner/repo --limit 20 --scan-limit 100
openrabbit docs --pr 42 --repo owner/repo --limit 5
openrabbit similar-issues --pr 42 --repo owner/repo --limit 5 --search-limit 20
```

## Workflow Controls

Every maintenance command returns `workflow_controls` in JSON output. Automation
should inspect this object before treating a run as mutating:

```json
{
  "mode": "dry_run",
  "dry_run": true,
  "required_permissions": ["pull_requests:read", "issues:read"],
  "github": {
    "requested": false,
    "mutates": false,
    "operation": "label_application",
    "status": "dry_run"
  },
  "files": {
    "requested": false,
    "mutates": false,
    "operation": "none",
    "status": "not_supported"
  },
  "managed_comment": {
    "enabled": false,
    "requested": false,
    "operation": "none",
    "status": "not_supported"
  }
}
```

For read-only commands, `mode` is `read_only`, `dry_run` is `true`,
`mutates_files` is `false`, and `mutates_github` is `false`. For
`labels --apply`, `mode` is `apply` and `github.requested` is `true`. A run
only reports `mutates_github: true` when GitHub accepted at least one label
application.

## GitHub Actions

Use [examples/github-actions/openrabbit-maintenance.yml](../examples/github-actions/openrabbit-maintenance.yml)
when maintainers want a manual Actions button for maintenance commands.

The example uses `workflow_dispatch` and supports:

- `labels`, with optional existing-label application.
- `changelog`, with optional `since`, `until`, `notes`, `limit`, and
  `scan_limit` inputs.
- `docs`, for pull request documentation follow-ups.
- `similar-issues`, for pull request issue lookup.

Keep `apply_labels` false until a maintainer has reviewed the proposed labels.
The workflow declares `issues: write` because it can run `labels --apply`; remove
that permission if you only want read-only maintenance runs.

## Privacy Boundaries

- Maintenance commands do not call a model.
- Local release-note files are sanitized, bounded, and marked as truncated when
  their excerpt is capped.
- Documentation suggestions summarize changed public surfaces and do not echo
  raw patch bodies.
- Similar issue search builds a bounded, redacted query from PR metadata and
  linked issue context.
- GitHub issue search is best effort. If search fails, explicitly linked issues
  can still be returned.
- Local memory remains under `.openrabbit/` unless you explicitly export it.

## Troubleshooting

`no GitHub token found`

Set `OPENRABBIT_GITHUB__TOKEN` or `GITHUB_TOKEN`. In GitHub Actions, pass
`${{ github.token }}` through `OPENRABBIT_GITHUB__TOKEN`.

`labels --apply` reports `permission_failed`

The token can read the pull request but cannot write issue labels. Add
`issues: write` to the workflow permissions or use a token with issue-label
write access.

Proposed labels are skipped as `not_in_repository`

OpenRabbit does not create repository labels. Create the label in GitHub first,
then rerun `openrabbit labels --apply`.

`changelog` returns fewer pull requests than expected

Check the `--since`, `--until`, `--limit`, and `--scan-limit` values. The command
only drafts from merged pull requests visible to the token.

`docs` returns no suggestions

The pull request may not touch recognized public surfaces such as CLI commands,
configuration, examples, connectors, memory, eval, RAG, or release notes.

`similar-issues` returns only linked issues

GitHub issue search may be unavailable or the generated query may not match other
issues. Increase `--search-limit` or refine the pull request title/body so the
bounded query has clearer terms.
