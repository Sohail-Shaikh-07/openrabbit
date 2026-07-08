# Repository Guidelines

OpenRabbit can index repository-owned guideline files and pass the relevant
snippets into review, describe, ask, and improve prompts when RAG context is
available.

Guidelines are local-first. They are read from the repository checkout during
`openrabbit index`, embedded locally, and stored in the Qdrant `rules`
collection. They do not create memory rows, GitHub comments, or hosted service
calls by themselves.

## Detected Files

OpenRabbit treats these files as review rules:

- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `.cursorrules`
- `.github/copilot-instructions.md`
- `.github/instructions/*.instructions.md`
- `.windsurfrules`
- `.rules/**`
- `.openrabbit/*`
- `.codereviewer/*`

The `.openrabbit/*` rules remain the OpenRabbit-native place for project
architecture notes, coding rules, security rules, and review examples. Legacy
`.codereviewer/*` files are still indexed for compatibility.

## Scope

Root guideline files apply globally.

Path-local guideline files apply to their directory subtree. For example:

```text
services/api/AGENTS.md
```

is indexed as a repository guideline with scope `services/api`.

Global provider-style files such as `.github/copilot-instructions.md`,
`.github/instructions/*.instructions.md`, and `.rules/**` are indexed with
scope `.`.

## Prompt Labels

When guideline snippets are retrieved, prompt context labels them with source
and scope:

```text
[repository guideline services/api/AGENTS.md (scope: services/api)]
```

Verbose review output also includes the guideline metadata in context
provenance when it is available.

## Updating Guidelines

Run indexing after changing guideline files:

```bash
openrabbit index --workspace .
```

If Qdrant is unavailable, OpenRabbit still reviews from the PR diff and local
memory, but guideline snippets will not be available until the repository index
is loaded.
