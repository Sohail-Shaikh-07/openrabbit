# AST Review Controls

AST review controls add declarative review guidance to changed functions,
methods, and classes. They complement path controls and path instructions. A
rule is included only when a supported symbol overlaps an added line in the
pull request.

## Configuration

Add `ast_instructions` under `review` in `.openrabbit/config.yml`:

```yaml
review:
  security: true
  performance: true
  architecture: true
  bug: true
  test_coverage: true
  style: false
  profile: assertive
  path_include: []
  path_exclude: []
  path_instructions: []
  ast_instructions:
    - path: "src/api/**"
      languages: [python]
      symbols: [function, method]
      name_pattern: "*_task"
      instructions: "Require explicit authorization before mutating task data."
  max_files: 80
  max_changed_lines: 4000
  include_generated: false
```

Each rule has the following fields:

* `path` is required and matches the repository-relative changed-file path.
* `languages` is optional. It accepts `python`, `javascript`, and `typescript`.
  An empty list matches every supported language.
* `symbols` is required and contains one or more of `function`, `method`, and
  `class`.
* `name_pattern` is optional and defaults to `*`.
* `instructions` is required, non-empty review guidance.

Values are validated before GitHub review work starts. Unknown fields,
unsupported languages, unsupported symbol kinds, empty symbol lists, and empty
text fields are configuration errors. Duplicate language and symbol values are
deduplicated while preserving their first-seen order.

## Matching Semantics

Paths and symbol names use shell-style glob matching. `*` matches any sequence
of characters, `?` matches one character, and bracket expressions such as
`[ab]` match one character from the set. Use a path such as `src/api/**` for a
tree of repository-relative paths. Matching is deterministic and rules are
applied in configuration order; overlapping rules are additive.

Supported source extensions are:

* `.py`, normalized as `python`
* `.js` and `.jsx`, normalized as `javascript`
* `.ts` and `.tsx`, normalized as `typescript`

The supported symbol kinds are `function`, `method`, and `class`. A method is a
function declared inside a class. AST rules use the PR-head source file, not a
partial patch. A rule matches only when the symbol's inclusive new-file line
span contains at least one added line from the diff. This is addition-only
symbol overlap: deleted-only declarations and context-only symbols do not
receive AST guidance. Multiple rules and symbols may match the same file.

## Source Loading And Limits

OpenRabbit requests PR-head source only for kept, non-binary files whose path
and language can match at least one AST rule. Existing include, exclude,
generated-file, file-count, changed-line, and binary-file controls are applied
first.

Source loading has these fixed bounds:

* At most `524288` decoded source bytes are loaded per file.
* At most four source requests run concurrently.
* When `ast_instructions` is empty, OpenRabbit makes no AST source requests.
* Unsupported extensions are skipped without a source request.

These controls do not change the existing path filtering behavior. Matched AST
guidance is added to prompt context with the file path, line span, language,
symbol kind, and symbol name as provenance.

## Fallback Behavior

AST controls fail open so a source problem does not make a pull request
unreviewable. If source is unavailable, oversized, or unparsable, OpenRabbit
records a sanitized warning and falls back to the available path and diff
context for that file. Other files continue to be processed. Unsupported
extensions produce no AST match and do not require a warning.

Warnings do not include source bodies, credentials, or arbitrary exception
messages. Invalid configuration is different: it stops before review work so
that a misspelled or unsafe rule cannot silently change review behavior.

## Security Boundary

AST instructions are untrusted repository text. They are prompt guidance only.
Reviewed source code is never imported or executed, and code or configuration
rules are never executed. AST rules do not emit findings directly, run shell
commands, load plugins, or override the required output schema, evidence rules,
or safety constraints.

## Examples

Security guidance for changed authorization methods:

```yaml
review:
  ast_instructions:
    - path: "src/auth/**"
      languages: [python, javascript, typescript]
      symbols: [method, function]
      name_pattern: "*permission*"
      instructions: "Check authorization and tenant boundaries before data access."
```

Test guidance for changed test functions:

```yaml
review:
  ast_instructions:
    - path: "tests/**"
      languages: [python]
      symbols: [function, method]
      name_pattern: "test_*"
      instructions: "Keep assertions focused on observable behavior and failure cases."
```

Architecture guidance for changed service classes:

```yaml
review:
  ast_instructions:
    - path: "src/services/**"
      languages: [typescript]
      symbols: [class]
      name_pattern: "*Service"
      instructions: "Keep domain logic separate from transport and persistence adapters."
```

AST rules never replace `path_instructions`; both may appear in the same review.
