# Interactive PR Workflows

OpenRabbit v1.8 adds a small set of interactive PR commands that are useful
from a terminal, PR comments, and GitHub Actions logs. The default posture is
read-only. Commands that write to GitHub require an explicit publish flag or a
polling comment command that maps to a managed OpenRabbit comment.

## Command Matrix

| Command | Default | Optional write | Stable JSON fields |
| --- | --- | --- | --- |
| `openrabbit describe` | Read-only PR summary and walkthrough | `--publish` creates or updates one managed summary comment | `schema_version`, `command`, `description`, `managed_summary`, `publish_status`, `context_diagnostics` |
| `openrabbit ask` | Read-only answer to one PR question | `--publish` creates or updates one managed answer comment | `schema_version`, `command`, `answer`, `ask_focus`, `managed_answer`, `publish_status`, `context_diagnostics` |
| `openrabbit improve` | Read-only changed-line improvement suggestions | `--publish` posts grounded suggestions to a PR review | `schema_version`, `command`, `suggestions`, `suggestion_quality`, `publish_status`, `context_diagnostics` |
| `openrabbit labels` | Read-only label proposals | `--apply` adds proposed labels that already exist in the repository | `schema_version`, `command`, `current_labels`, `repository_labels_loaded`, `label_proposals`, `label_application`, `workflow_controls`, `mutates_github` |

`review` remains the main review command. This guide focuses on the interactive
commands that help maintainers inspect, ask, improve, and triage a pull request.

## JSON Output Contracts

Use `--format json` when another script needs to consume command output. Each
interactive JSON payload starts with:

```json
{
  "schema_version": "1.0",
  "command": "describe"
}
```

The `schema_version` field lets automation reject unknown shapes before reading
command-specific fields. The `command` field helps shared wrappers distinguish
`describe`, `ask`, `improve`, and `labels` output.

Common metadata fields include `repo`, `number`, `title`, `state`, `head_sha`,
`files_changed`, `binary_files`, `hunks`, and `commits`.

Context-aware commands also include `context_loaded`, `context_provenance`,
`context_diagnostics`, `connector_context`, `conversation_count`, and
`learning_count`. These diagnostics are for troubleshooting source selection and
do not include raw prompts, raw connector output, API keys, or credentials.

## Managed Publishing Controls

`describe --publish` creates or updates one OpenRabbit-managed PR summary
comment. JSON output includes `managed_summary` with the marker, status,
comment ID, and comment URL.

```bash
openrabbit describe --pr 42 --repo owner/repo --publish
openrabbit describe --pr 42 --repo owner/repo --format json
```

`ask --publish` creates or updates one OpenRabbit-managed answer comment for the
question. JSON output includes `managed_answer` with the marker, status,
comment ID, and comment URL.

```bash
openrabbit ask --pr 42 --repo owner/repo --publish "What should I review first?"
openrabbit ask --pr 42 --repo owner/repo --format json "What changed?"
```

Managed comments use recognizable OpenRabbit markers and are safe to rerun. A
new publish run updates the existing managed comment instead of creating a
duplicate.

## Line-Level Ask

Use `--file` and `--line` together when the question is about one changed
new-side line. The selected line must be part of the pull request diff.

```bash
openrabbit ask --pr 42 --repo owner/repo --file src/search.py --line 42 "Is this guard enough?"
openrabbit ask --pr 42 --repo owner/repo --file src/search.py --line 42 --format json "What can break here?"
```

When the line is valid, JSON output includes `ask_focus` with the file, line,
mode, validation status, and nearby diff evidence. When the line is not a
changed new-side line, the command exits with a user error instead of asking the
model to speculate.

## Improve Quality Output

`improve` suggests small fixes grounded to changed files and changed new-side
lines. It never applies patches or pushes commits.

```bash
openrabbit improve --pr 42 --repo owner/repo
openrabbit improve --pr 42 --repo owner/repo --format json
openrabbit improve --pr 42 --repo owner/repo --publish
```

JSON output includes `suggestion_quality`, which reports raw, grounded, kept,
and dropped suggestion counts. Dropped reasons include ungrounded suggestions,
vague advice without a fix, comment-only fixes, TODO/FIXME advice, broad
refactors without fixes, placeholder fixes, and unavailable security dependency
snippets.

## Label Proposals

`labels` proposes labels without mutating GitHub by default. It does not create
repository labels. Use `--apply` only when you want OpenRabbit to add proposed
labels that already exist in the repository label list.

```bash
openrabbit labels --pr 42 --repo owner/repo
openrabbit labels --pr 42 --repo owner/repo --limit 5
openrabbit labels --pr 42 --repo owner/repo --format json
openrabbit labels --pr 42 --repo owner/repo --apply
```

Each proposal includes a label name, confidence, reason, signals, and
`exists_in_repository`. A false `exists_in_repository` value means the label was
suggested from evidence but was not present in the repository label list fetched
from GitHub. Automation should treat these as suggestions for humans, not as a
permission to create labels.

JSON output includes `label_application` with the dry-run, applied, skipped, or
failed status. Permission failures are reported there instead of hiding which
labels were requested.

Maintenance command JSON also includes `workflow_controls`. This shared object
reports the mode, dry-run state, required permissions, GitHub write request and
status, file-write status, and managed-comment status. Changelog, docs, and
similar-issues use read-only controls. `labels --apply` is the only maintenance
command in this group that requests a GitHub write.

## PR Comment Commands

When `openrabbit start` is running, maintainers can trigger workflows from PR
comments:

```text
/openrabbit review
/openrabbit full review
/openrabbit improve
/openrabbit ask what changed in the search path?
/openrabbit summary
/openrabbit configuration
/openrabbit pause
/openrabbit resume
/openrabbit ignore
```

Comment commands are only handled by the polling service. One-off CLI commands
do not listen to PR comments. `/openrabbit summary` maps to the managed summary
comment. `/openrabbit pause` and `/openrabbit ignore` store local suppression
state until `/openrabbit resume` is received.

## GitHub Actions Usage

Use the review workflow when you want automatic review comments:

```text
examples/github-actions/openrabbit-review.yml
```

Use the interactive workflow when you want a manual Actions button for
summaries, questions, improvement suggestions, or labels:

```text
examples/github-actions/openrabbit-interactive.yml
```

The interactive example uses `workflow_dispatch`, resolves the requested PR
number, installs OpenRabbit, prepares a minimal config when needed, and runs one
selected command. It keeps publishing opt-in through the workflow input.

## Privacy And Safety Defaults

- `describe`, `ask`, `improve`, and `labels` are read-only by default.
- `labels --apply` is the only label write mode and only applies labels that
  already exist in the repository.
- Maintenance commands expose `workflow_controls` so automation can inspect
  mode, requested permissions, write status, and managed-comment support before
  trusting any mutation.
- `improve --publish` only posts grounded, actionable suggestions.
- `describe --publish` and `ask --publish` update one managed comment each.
- Connector snippets remain optional, bounded, source-labeled, and untrusted.
- Local PR memory and eval artifacts stay under `.openrabbit/` unless the user
  explicitly exports them.
