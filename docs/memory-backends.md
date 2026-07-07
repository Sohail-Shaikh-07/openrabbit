# OpenRabbit Memory Backend Design

OpenRabbit memory is local-first by default. The required backend is SQLite in
`.openrabbit/state/openrabbit.db`, and future graph or vector systems should be
optional plugin layers on top of the same review memory contract.

## Goals

- Keep the default review loop private, portable, and service-free.
- Let future graph and vector memory improve context without changing review
  orchestration.
- Make backend responsibilities explicit before adding heavier services.
- Keep secrets, tokens, and raw credentials out of every memory backend.

## Current Default

`SQLitePullRequestMemory` stores structured PR review memory:

- review runs by repository, PR number, head SHA, timestamp, context state, and
  publish state
- finding fingerprints and review status
- finding title, category, severity, file, line, reason, and suggestion
- first seen and last seen SHAs

SQLite is the only required backend because it is local, inspectable, durable,
and works without Docker, Qdrant, Neo4j, cloud storage, or hosted APIs.

## Adapter Boundary

Memory backends implement `PullRequestMemoryBackend` from `memory.backends`.
The contract stays intentionally small:

```python
load_history(repo, pr_number)
compare_with_history(repo, pr_number, head_sha, current_findings)
record_review(repo, pr_number, head_sha, findings, context_loaded, comments_posted)
```

The review pipeline should depend on this contract, not on SQLite tables or any
specific future service. Export and prune remain SQLite maintenance operations
until a future backend opts into equivalent maintenance APIs.

## Plugin Responsibilities

A future memory plugin may enrich the review context, but it must not become
required for basic review execution.

Every plugin should provide:

- a clear install path outside the default package dependencies
- a health check that reports availability without mutating state
- deterministic failure behavior that falls back to SQLite or diff-only context
- documented storage location and deletion/export behavior
- explicit limits for retained text, embeddings, and metadata

Every plugin must avoid:

- storing GitHub tokens, model API keys, or provider credentials
- requiring network access unless the user configured that backend
- changing finding fingerprints in a way that breaks incremental re-review
- posting comments directly to GitHub

## Graph Memory Plugin

A graph backend should model relationships that are hard to express in flat
SQLite rows.

Useful node types:

- repository
- pull request
- commit
- review run
- finding fingerprint
- file path
- symbol
- reviewer or commenter
- linked issue
- explicit learning

Useful edges:

- `PR_CHANGED_FILE`
- `PR_HAS_COMMIT`
- `RUN_REPORTED_FINDING`
- `FINDING_TOUCHES_SYMBOL`
- `FINDING_STILL_PRESENT`
- `FINDING_POSSIBLY_FIXED`
- `COMMENT_REFERENCES_FINDING`
- `PR_LINKS_ISSUE`
- `LEARNING_APPLIES_TO_PATH`

The graph backend should answer questions such as:

- Has this root cause appeared before in this repository?
- Which files often change together with this file?
- Which past PRs fixed similar findings?
- Which user comments or learnings apply to this path?

Graph memory should return compact structured context. It should not dump full
discussion threads or entire file contents into prompts.

## Vector Memory Plugin

A vector backend should retrieve semantically similar review knowledge.

Good candidates for embeddings:

- concise prior finding summaries
- accepted fix summaries
- explicit `@openrabbit learn` instructions
- repository guideline snippets
- linked issue summaries
- review examples

Poor candidates for embeddings:

- raw tokens or secrets
- full PR diffs without chunking
- entire discussion threads
- generated files
- duplicate copies of repository source already handled by RAG

Vector memory should be separate from repository RAG. Repository RAG answers
"what does this codebase contain?" Memory retrieval answers "what has this repo
learned from prior reviews?"

## Configuration Shape

The default configuration should remain simple:

```yaml
memory:
  enabled: true
  path: state/openrabbit.db
```

A future plugin can extend this without breaking the default:

```yaml
memory:
  enabled: true
  path: state/openrabbit.db
  plugins:
    graph:
      enabled: false
      provider: neo4j
    vector:
      enabled: false
      provider: qdrant
```

Plugin dependencies should live behind optional install extras or separate
packages. Enabling a plugin without installing or starting its service should
produce a clear warning and continue with SQLite memory.

## Context Flow

The expected future flow is:

```text
GitHub PR
  -> SQLite memory loads deterministic PR state
  -> optional graph plugin loads relationship context
  -> optional vector plugin loads similar review knowledge
  -> context builder merges compact memory context
  -> review, describe, ask, and improve prompts receive labeled sources
```

Prompt context should label memory sources clearly:

```text
Memory context:
- SQLite: prior finding statuses for this PR
- Graph: related past PRs and linked issue relationships
- Vector: similar accepted findings and explicit learnings
```

## Failure Behavior

Memory is helpful context, not a hard dependency.

If SQLite is disabled, OpenRabbit should continue without local review memory.
If an optional plugin is unavailable, OpenRabbit should continue with SQLite
memory. If both SQLite and plugins are unavailable, OpenRabbit should still run
from the PR diff and any available RAG context.

## Version Plan

v1.3 keeps SQLite as the only required backend and documents graph/vector
boundaries.

Later phases can add plugin adapters in this order:

1. explicit learnings in SQLite
2. guideline and issue context in prompt memory
3. optional graph adapter for relationships
4. optional vector adapter for semantic review learnings
5. dashboards and evaluation metrics for memory quality

## Acceptance Rules For Future Backends

A backend should not be added to the default runtime unless all of these are
true:

- it improves review quality in evaluation scenarios
- it has tests for failure and fallback behavior
- it does not store secrets
- it has export or deletion documentation
- it is optional unless it replaces SQLite with a simpler local default
- it keeps review comments grounded to changed lines
