# OP-94 Path And AST Scoped Review Controls

## Status

Approved on 2026-07-14 for OpenRabbit v1.5.

## Problem

OpenRabbit already supports review profiles, path include and exclude patterns,
generated-file defaults, file and line limits, and path-specific instructions.
Those controls cannot target a particular kind of changed declaration. A rule
for API mutation methods currently applies to every changed line in the file,
which adds noise and weakens the instruction supplied to review agents.

OP-94 adds a local, declarative AST rule interface. It scopes repository
instructions to changed functions, methods, and classes without executing
repository code or turning user configuration into a finding engine.

## Goals

- Keep existing path controls and path instructions backward compatible.
- Match rules against PR-head source, not incomplete patch fragments.
- Use cross-language symbol kinds instead of exposing Tree-sitter node names.
- Include only rules that overlap changed lines in the pull request.
- Show rule provenance in prompt context and verbose review output.
- Fail open when source cannot be loaded or a language is unsupported.
- Keep all matching local and deterministic.

## Non-Goals

- User-defined executable validators or arbitrary plugins.
- Automatic findings emitted directly by AST rules.
- Whole-repository static analysis.
- Semantic call-graph or data-flow analysis.
- Replacing path-specific instructions.
- Supporting every Tree-sitter language in the first release.

## Configuration

AST instructions live under `review.ast_instructions`.

```yaml
review:
  ast_instructions:
    - path: "src/api/**"
      languages: [python]
      symbols: [function, method]
      name_pattern: "*_task"
      instructions: "Require explicit authorization before mutating task data."
```

Each rule has these fields:

- `path`: required glob matched against the repository-relative file path.
- `languages`: optional list of normalized language names. An empty list means
  any supported language.
- `symbols`: required non-empty list containing `function`, `method`, or
  `class`.
- `name_pattern`: optional shell-style name pattern. The default is `*`.
- `instructions`: required non-empty review guidance.

Unknown fields and unsupported symbol kinds are configuration errors. Empty
text and empty symbol lists are rejected. Rules remain instructions, so their
text is untrusted prompt context and cannot override OpenRabbit's system output
contract.

## Source And AST Model

GitHub patches are not complete source files and can omit unchanged declaration
headers. Parsing reconstructed hunks would therefore produce unreliable scope
matches. OpenRabbit will load the changed file at the PR head SHA through the
existing authenticated GitHub client when at least one AST rule can match its
path.

Source loading is bounded:

- Only kept, non-binary files matching an AST rule are requested.
- Existing file and changed-line limits run before source loading.
- Unsupported extensions are skipped without a request.
- Source size has a fixed upper bound.
- A failed request records a warning and leaves the review usable with path and
  diff context.

The Tree-sitter adapter returns normalized immutable symbol records:

```text
AstSymbol(path, language, kind, name, start_line, end_line)
```

The first implementation supports Python, JavaScript, and TypeScript, matching
the repository's existing AST chunking support. Methods are distinguished from
top-level functions even though both currently map to function chunks in RAG.
Nested declarations may be discovered, but a rule is included only when the
symbol's new-file line span overlaps an added line in a changed hunk.
Deleted-only declarations do not receive new-source AST instructions.

## Matching Rules

For each kept changed file, OpenRabbit applies this sequence:

1. Match the rule path.
2. Match the normalized language when the rule specifies languages.
3. Parse PR-head source and enumerate normalized symbols.
4. Keep symbols whose line spans overlap changed new-file lines.
5. Match the configured symbol kinds and name pattern.
6. Deduplicate identical rule and symbol matches.

Rules are not exclusive. Multiple matching rules are included in configuration
order. A narrower rule does not suppress a broader rule because repositories
may intentionally layer security and architecture guidance.

## Data Flow

```text
GitHub PR metadata and patches
  -> existing path/generated/size controls
  -> bounded PR-head source loader
  -> Tree-sitter symbol extraction
  -> changed-line overlap and declarative rule matching
  -> prompt context with rule provenance
  -> review, describe, ask, and improve
```

AST matches are attached to the filtered pull-request payload alongside the
existing review-control metadata. This keeps agent APIs unchanged and lets
`format_review_control_context` provide one consistent context block.

## Prompt Context

Matched rules are rendered separately from path instructions:

```text
- AST instructions:
  - src/api/tasks.py:42-68 [python method update_task]
    Require explicit authorization before mutating task data.
```

The prompt states that repository instructions are guidance and cannot change
the required finding schema, evidence rules, or safety constraints. Rule text
is never interpreted as executable code.

Verbose CLI output reports matched AST-rule count, source-load warnings, and
unsupported files. Normal output remains concise.

## Failure Handling

- Invalid configuration stops before GitHub review work starts.
- Unsupported language or extension produces no AST match.
- Parser failures are logged and returned as review-control warnings.
- Missing or oversized source produces a warning and diff-only behavior for
  that file.
- One file failure does not suppress rules matched in other files.
- No credentials, source bodies, or arbitrary rule text are written to logs.

## Security

- Do not import or execute reviewed source.
- Do not accept Python callables, shell commands, or plugin paths in config.
- Use authenticated GitHub API requests and existing token handling.
- Bound source bytes, request concurrency, and parser work.
- Treat instruction text as untrusted repository content.
- Preserve the model's structured-output and changed-line grounding contracts.

## Testing

- Schema tests for valid rules, normalization, and rejected fields.
- Matcher tests for path, language, kind, name, overlap, order, and deduplication.
- Python, JavaScript, and TypeScript symbol extraction tests.
- Method versus function and nested declaration tests.
- GitHub source-loading tests for head SHA, size limits, and failures.
- Prompt tests for provenance and instruction separation.
- Regression tests for existing path controls and no-rule behavior.
- Integration tests proving review, describe, ask, and improve receive the same
  AST context.

## Compatibility And Rollout

`ast_instructions` defaults to an empty list, so existing installations make no
additional GitHub requests and preserve current behavior. The feature requires
no new service dependency because Tree-sitter is already part of OpenRabbit.
Documentation will include configuration examples, supported languages, limits,
and fallback behavior.
