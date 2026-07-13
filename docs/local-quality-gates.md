# Local Quality Gates

OpenRabbit can run deterministic repository tools before its review agents. The results are normalized into structured diagnostics, shown in the review summary, included as prompt evidence, and written to `openrabbit eval` reports.

Quality gates are local-only subprocesses and are disabled by default. OpenRabbit executes known argument lists directly with `shell=False`; configuration cannot provide arbitrary commands.

## Configuration

Enable safe auto-detection:

```yaml
quality:
  enabled: true
  auto_detect: true
  tools: []
  timeout_seconds: 120
  max_output_chars: 20000
  max_diagnostics: 100
```

Or choose an exact tool set:

```yaml
quality:
  enabled: true
  auto_detect: false
  tools:
    - ruff
    - mypy
    - pytest
  timeout_seconds: 180
```

Supported names are `ruff`, `mypy`, `pytest`, `bandit`, `semgrep`, `eslint`, and `npm-test`. Explicitly selected tools that are not installed appear as `unavailable`; they do not abort the review.

## Detection

Auto-detection requires both a local installation and repository evidence:

| Tool | Repository evidence |
| --- | --- |
| Ruff | `[tool.ruff]` in `pyproject.toml` |
| mypy | `[tool.mypy]` in `pyproject.toml` or `mypy.ini` |
| pytest | `tests/` or pytest configuration in `pyproject.toml` |
| Bandit | `.bandit` or Bandit configuration in `pyproject.toml` |
| Semgrep | `.semgrep.yml` or `.semgrep.yaml` |
| ESLint | ESLint dependency or a recognized ESLint config file |
| npm test | A `test` script in `package.json` |

Semgrep always uses the checked-in local rule file. OpenRabbit does not use `--config auto`, so enabling Semgrep does not fetch a remote ruleset. ESLint prefers the repository-local `node_modules/.bin/eslint` executable.

## Runtime Safety

Every tool has the configured timeout. Captured stdout and stderr are bounded before parsing, raw process output is not written to review or eval reports, and only a bounded number of normalized diagnostics enters model context.

Run OpenRabbit from the local checkout that matches the GitHub repository passed to `--repo`. Quality gates analyze the local working tree, while the PR diff is fetched from GitHub. OpenRabbit does not silently clone or mutate repositories.

`npm-test` executes the repository's own package script. Enable it only for repositories whose scripts you trust. The same principle applies to test plugins and analyzer configuration loaded by any selected tool.

## Privacy Boundary

Tool execution stays local. Normalized diagnostic fields such as file, line, rule code, and message are included in agent prompts. With Ollama, that context stays on the local machine. With OpenAI or another API provider, those diagnostics are sent to the configured endpoint along with the rest of the review prompt.

## Review And Eval Output

`openrabbit review` prints status counts such as `passed=2, failed=1` plus the diagnostic total. The structured summary contains `quality_gates`, `quality_status_counts`, `quality_diagnostics_count`, and `quality_error`.

`openrabbit eval` preserves each sanitized gate result in the JSON report and adds aggregate quality status and diagnostic counts. The Markdown report includes compact quality columns for regression comparison.
