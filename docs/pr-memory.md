# OpenRabbit PR Memory

OpenRabbit keeps a local structured memory of pull request review runs and loads current pull request conversation context from GitHub. The goal is to make re-reviews smarter without sending repository history to a hosted service.

## What Is Stored

The local memory stores derived review metadata:

- repository name and PR number
- reviewed head SHA
- whether repository context was loaded
- whether comments were posted
- finding fingerprints
- finding status, title, category, severity, file, line, reason, and suggestion
- first seen and last seen SHAs

OpenRabbit also fetches PR reviews, inline review comments, and issue comments for `review`, `describe`, `ask`, and `improve` when memory is enabled. Those conversation events are normalized into a `PullRequestHistory` object for prompt context.

## What Is Not Stored

OpenRabbit does not store GitHub tokens or model API keys in memory. Secrets should stay in environment variables.

The memory database is local state. Do not commit it to the repository.

GitHub conversation events are used as prompt context for the current command. They are sanitized before prompt use, common token-like strings are redacted, and large comment bodies are trimmed. Ordinary comments are not saved as permanent learnings.

## Default Location

By default, repository-level memory is stored under:

```text
.openrabbit/state/openrabbit.db
```

`openrabbit init` creates `.openrabbit/.gitignore` with local state paths ignored:

```text
state/
memory/
cache/
*.db
```

## Configuration

The default config enables local memory:

```yaml
memory:
  enabled: true
  # Local SQLite memory is stored under .openrabbit/state by default.
  # path: state/openrabbit.db
  learnings_enabled: true
```

To disable memory:

```yaml
memory:
  enabled: false
```

To store memory somewhere else:

```yaml
memory:
  enabled: true
  path: C:/Users/you/AppData/Local/OpenRabbit/memory/my-repo.db
```

Relative paths are resolved from `.openrabbit/`.

To disable explicit learning while keeping finding memory:

```yaml
memory:
  enabled: true
  learnings_enabled: false
```

## Finding Status

OpenRabbit fingerprints findings by root cause instead of exact wording. That lets it recognize the same issue even when the model changes phrasing.

Statuses:

- `new`: not seen before on this PR
- `still_present`: seen before and still found
- `possibly_fixed`: seen before but missing from the current review
- `stale`: missing for more than one review after being marked possibly fixed

## Incremental Re-Review

`openrabbit review` supports two modes:

```bash
openrabbit review --pr 42 --repo owner/repo --mode incremental
openrabbit review --pr 42 --repo owner/repo --mode full
```

`incremental` is the default. It still runs the review and records all current findings, but it only publishes findings whose memory status is `new`. Findings marked `still_present` remain visible in the local summary and memory database, but OpenRabbit does not repost them as duplicate GitHub comments.

`full` publishes every grounded finding from the current run. Use this when you intentionally want to refresh all review comments.

Review summaries show the previous reviewed SHA and status counts when memory data is available. That makes it easier to see whether a run found new issues, repeated issues, or findings that are now possibly fixed or stale.

## Inspecting Memory

Use the read-only memory command to inspect what OpenRabbit remembers for a PR:

```bash
openrabbit memory --pr 42 --repo owner/repo
openrabbit memory --pr 42 --repo owner/repo --format json
openrabbit memory --learnings --repo owner/repo
```

The command prints the configured memory database path, the last reviewed SHA, finding counts by status, and stored finding fingerprints. It does not fetch GitHub data, call a model, create a database, or post anything to the pull request.

To export repository memory to a deterministic JSON file:

```bash
openrabbit memory --repo owner/repo --export .openrabbit/reports/memory.json
```

To prune local memory rows older than a date:

```bash
openrabbit memory --repo owner/repo --prune-before 2026-01-01
```

Export and prune are repository-level operations. Run them separately so a destructive prune cannot be hidden behind an export command.

## Local Review Learnings

When `openrabbit start` is running, maintainers can teach repository-specific review preferences with an explicit PR comment command:

```text
@openrabbit learn Prefer SQLAlchemy bind parameters for any raw SQL in repositories.
```

OpenRabbit stores the instruction locally with the repository, source PR, source comment, author, timestamp, and active flag. Active learnings are included in the `PR history context` used by `review`, `describe`, `ask`, and `improve`.

Learnings are treated as instructions, not findings. OpenRabbit does not infer permanent learnings from ordinary human comments.

## GitHub Conversation Context

When memory is enabled, model-facing commands load the live PR conversation before generating output:

- submitted PR reviews
- inline review comments
- top-level PR issue comments

This helps OpenRabbit understand whether a maintainer already asked for a change, whether the author says a fix was pushed, and what previous bot or human review context exists. If GitHub conversation loading fails, OpenRabbit logs a warning and continues with local memory, linked issues, RAG context, and the diff.

## Why SQLite First

SQLite is the first memory backend because it is local, portable, inspectable, and has no service dependency. Graph and vector memory are intentionally future plugin layers. They should enrich retrieval later without becoming required for the core review loop. The adapter design and future plugin boundaries are documented in [memory-backends.md](memory-backends.md).

## Backend Contract

Memory backends implement `PullRequestMemoryBackend` from `memory.backends`. The contract is intentionally small:

- `load_history(repo, pr_number)`
- `compare_with_history(repo, pr_number, head_sha, current_findings)`
- `record_review(repo, pr_number, head_sha, findings, context_loaded, comments_posted)`

SQLite is the default implementation. Future graph or vector memory adapters should implement the same contract and remain optional, local-first plugin layers.
