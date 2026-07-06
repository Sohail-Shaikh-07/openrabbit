# OpenRabbit PR Memory

OpenRabbit keeps a local structured memory of pull request review runs. The goal is to make re-reviews smarter without sending repository history to a hosted service.

## What Is Stored

The local memory stores derived review metadata:

- repository name and PR number
- reviewed head SHA
- whether repository context was loaded
- whether comments were posted
- finding fingerprints
- finding status, title, category, severity, file, line, reason, and suggestion
- first seen and last seen SHAs

OpenRabbit also has typed GitHub client support for fetching PR reviews, inline review comments, and issue comments. Those conversation events are normalized into a `PullRequestHistory` object for prompt context.

## What Is Not Stored

OpenRabbit does not store GitHub tokens or model API keys in memory. Secrets should stay in environment variables.

The memory database is local state. Do not commit it to the repository.

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

## Finding Status

OpenRabbit fingerprints findings by root cause instead of exact wording. That lets it recognize the same issue even when the model changes phrasing.

Statuses:

- `new`: not seen before on this PR
- `still_present`: seen before and still found
- `possibly_fixed`: seen before but missing from the current review
- `stale`: reserved for future re-review cleanup

## Why SQLite First

SQLite is the first memory backend because it is local, portable, inspectable, and has no service dependency. Graph and vector memory are intentionally future plugin layers. They should enrich retrieval later without becoming required for the core review loop.
