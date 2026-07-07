# Changelog

All notable changes to OpenRabbit are documented in this file.

## v1.2.0 - 2026-07-07

OpenRabbit v1.2.0 adds PR memory, incremental re-review, command-driven automation, stronger repository context controls, and repeatable PR-based quality logs.

### Review Memory And Re-Review

- Added local PR conversation memory for prior review findings, review comments, issue comments, commits, and prompt-ready PR history.
- Added finding fingerprints so review runs can classify findings as new, still present, or possibly fixed.
- Added incremental review mode so repeated reviews avoid reposting unchanged findings.
- Added full review mode for intentionally republishing all grounded findings.

### Improve Publishing And PR Commands

- Added explicit `openrabbit improve --publish` support for grounded suggestions.
- Added GitHub suggestion blocks for concrete replacement snippets and grouped broader suggestions in review bodies.
- Added PR comment command handling in polling mode for `@openrabbit review`, `full review`, `improve`, `ask`, `pause`, and `resume`.
- Added local paused-state and command cursor persistence under `.openrabbit/`.

### Repository Context And Controls

- Added `openrabbit index --health` for Qdrant connectivity and collection checks.
- Added changed-symbol retrieval hints and all-changed-file source filtering for RAG.
- Added context provenance in review summaries so users can see which indexed files influenced a run.
- Added CodeRabbit-style review controls for `chill` and `assertive` profiles, path include/exclude filters, path-specific instructions, max-file and max-line limits, generated-file defaults, and skipped-path reporting.

### Evaluation

- Added `openrabbit eval` to run selected PRs as dry-run regression scenarios.
- Added JSON and Markdown quality logs that capture command, PR, provider, model, context mode, findings, categories, dropped findings, skipped paths, runtime, and failures.

### Documentation

- Updated README coverage for PR memory, incremental review, improve publishing, PR commands, RAG health checks, review controls, and eval logs.
- Updated GitHub Actions and PR-Agent gap documentation for the new review automation and quality evidence loop.

### Release Notes

- Package version is `1.2.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- The fine-tuning dependency group remains optional and is intended for GPU environments.
- PyPI publishing requires a `PYPI_TOKEN` repository secret.

## v1.1.0 - 2026-07-06

OpenRabbit v1.1.0 expands the local-first reviewer with PR exploration commands, API-provider support, stronger config ergonomics, and benchmark corpus support.

### Review Workflow

- Added `openrabbit describe` for read-only PR summaries, changed-file walkthroughs, risk areas, and testing focus.
- Added `openrabbit ask` for evidence-based questions about a pull request using diff, metadata, changed-line evidence, and retrieved repository context.
- Added `openrabbit improve` for grounded, read-only improvement suggestions on changed lines.
- Added changed-line evidence and grounding so model output is filtered to changed files and changed new-side lines.
- Added token-aware PR diff compression for larger pull requests.

### Model Providers

- Added a shared model-provider contract across the review and PR exploration commands.
- Added support for the official OpenAI API provider.
- Added support for OpenAI-compatible base URLs for local gateways, self-hosted runtimes, and compatible hosted endpoints.
- Added provider API-key handling through environment-variable names instead of inline config secrets.

### Configuration

- Added layered config loading with built-in defaults, optional `~/.openrabbit/config.yml`, repository config, and `OPENRABBIT_...` environment overrides.
- Preserved legacy `.codereviewer/config.yml` compatibility while keeping `.openrabbit/config.yml` as the preferred repo config.
- Added Windows persistent environment fallback for GitHub tokens and model API keys.

### Automation And Evaluation

- Added a copyable GitHub Action recipe for self-hosted or configured-runner review workflows.
- Added a packaged v1.1 benchmark corpus covering security, authorization, performance, correctness, test-coverage, and large-diff review cases.
- Added loader validation and tests for the packaged benchmark corpus.

### Documentation

- Updated README coverage for current CLI commands, API providers, config layering, GitHub Actions, and benchmark usage.
- Added model provider documentation for Ollama, OpenAI, and OpenAI-compatible endpoints.
- Added benchmark corpus documentation and v1.1 release notes.
- Updated the PR-Agent gap analysis to reflect completed v1.1 capabilities and remaining roadmap items.

### Release Notes

- Package version is `1.1.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- The fine-tuning dependency group remains optional and is intended for GPU environments.
- PyPI publishing requires a `PYPI_TOKEN` repository secret.

## v1.0.0 - 2026-07-01

OpenRabbit v1.0.0 is the first complete release of the self-hosted AI pull request reviewer. It ships the full local-first review loop: GitHub polling, repository-aware retrieval, multi-agent analysis, comment ranking, fine-tuning utilities, benchmark tooling, and release-ready documentation.

### Foundation

- Added the Python package layout, Typer CLI, configuration loader, Dockerfile, Docker Compose setup, and test infrastructure.
- Added Ruff, Black, mypy, pytest, coverage, and CI validation.
- Added repository initialization templates for `.codereviewer/` configuration files.

### GitHub Integration

- Added GitHub REST client models, pull request parsing, diff extraction, repository discovery, polling state, and comment publishing support.
- Added manual review command support for targeted PR review and dry-run workflows.
- Added polling state persistence so updated PRs and commits can be detected across runs.

### Repository-Aware RAG

- Added repository scanning, language-aware chunking, embedding generation, Qdrant indexing, and retrieval.
- Added support for indexing source files, tests, docs, architecture notes, coding rules, security rules, and review examples.
- Added focused tests for scanner, chunker, embeddings, vector store, indexer, and retriever behavior.

### Multi-Agent Review

- Added security, performance, bug detection, architecture, and test coverage agents with typed finding contracts.
- Added a coordinator for parallel agent execution and result aggregation.
- Added ranking logic for deduplication, severity weighting, and low-signal filtering.

### Fine-Tuning Pipeline

- Added dataset loading, cleaning, instruction formatting, evaluation, QLoRA trainer configuration, training entry point, and adapter packaging.
- Added optional fine-tuning dependencies so normal installs stay lightweight.
- Added `openrabbit install-model` for installing the OpenRabbit reviewer adapter.

### Evaluation And Release

- Added benchmark schemas, runners, scorers, precision and recall reporting, and per-agent timing instrumentation.
- Added cross-platform smoke tests for Linux, macOS, and Windows.
- Updated README and CONTRIBUTING for the v1.0.0 workflow, commands, configuration, benchmarks, and contributor checks.
- Added CI validation for linting, formatting, type checking, tests, coverage, Docker build, Docker Compose config, and install smoke checks.

### Release Notes

- Package version is `1.0.0`.
- Python support is `>=3.12,<3.14`.
- The fine-tuning dependency group remains optional and is intended for GPU environments.
- PyPI publishing requires a `PYPI_TOKEN` repository secret.
