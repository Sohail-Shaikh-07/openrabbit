# Changelog

All notable changes to OpenRabbit are documented in this file.

## Unreleased

- Stabilized real-world review memory and ranking by ignoring agent category drift in finding fingerprints, merging repeated audit-trail and pagination findings, and loading repository guideline files directly when Qdrant/RAG context is unavailable.
- Hardened `openrabbit improve --publish` so placeholder fixes and snippets that introduce unavailable `require_*` security dependencies are dropped instead of being posted to GitHub.
- Added daemon lifecycle support with `openrabbit start --once`, local daemon PID metadata, stale-state cleanup, and a working `openrabbit stop --workspace ...` command.
- Switched user-facing PR comment command examples and managed summary follow-ups to `/openrabbit ...`, while retaining legacy mention-trigger compatibility.

## v1.5.0 - 2026-07-17

OpenRabbit v1.5.0 closes the CodeRabbit-parity planning phase with stronger PR memory, command-driven summaries, local quality evidence, AST-scoped review controls, dashboard-ready eval reports, and optional knowledge connector boundaries.

### PR Memory And Conversation Context

- Added sanitized GitHub PR conversation history for `review`, `describe`, `ask`, and `improve`, including reviews, inline review comments, issue comments, and commits.
- Added stale finding transitions so repeated reviews can distinguish new, still-present, possibly-fixed, and stale findings more clearly.
- Added clearer review summary output for previous review SHA, memory context, and status counts.

### PR Commands And Managed Summaries

- Expanded polling-mode PR comment commands with `/openrabbit ignore`, `/openrabbit summary`, and `/openrabbit configuration`.
- Added persisted ignore state and secret-safe configuration replies for polling mode.
- Added managed PR walkthrough summaries through `openrabbit describe --publish` and `/openrabbit summary`, updating one stable OpenRabbit summary comment instead of posting duplicates.

### Local Quality Gates

- Added optional local execution for Ruff, mypy, pytest, Bandit, Semgrep, ESLint, and npm test.
- Added safe auto-detection, known command definitions, per-tool timeouts, bounded process output, and structured diagnostics without arbitrary shell configuration.
- Added quality gate evidence to review agent prompts, CLI summaries, and JSON/Markdown eval reports.
- Added setup, detection, safety, and provider privacy-boundary documentation.

### Review Controls

- Added AST-scoped review instructions for Python, JavaScript, and TypeScript symbols.
- Added prompt controls that combine path guidance, diff evidence, and AST matches without starving changed-line evidence.
- Added CLI summary fields for matched AST rules, unsupported AST files, and sanitized review-control warnings.
- Added docs for path and symbol matching, source loading limits, provenance, and untrusted repository text handling.

### Evaluation And Dashboards

- Added repeatable eval scenario groups.
- Added dashboard-ready JSON sections for cards, charts, command outcomes, context sources, tool findings, trend data, and scenario groups.
- Added Markdown dashboard sections for local evaluation reports.
- Added documentation for using eval reports as local quality evidence without sending code to a hosted analytics service.

### Knowledge Connector Design

- Added dependency-free optional knowledge connector contracts for future MCP, web search, multi-repo, Jira, Linear, and document context sources.
- Added prompt-safe connector item sanitization, finite score normalization, bounded output, deterministic ordering, and coverage gates.
- Documented privacy rules, fail-open behavior, read-only health checks, future configuration shape, and adapter acceptance rules.

### Documentation

- Updated README coverage for local quality gates, AST review controls, eval dashboard reports, managed PR summaries, and optional knowledge connector contracts.
- Added local quality gate, AST review control, eval reporting, and optional knowledge connector documentation.
- Updated GitHub Actions release pinning guidance and PR-Agent gap analysis for v1.5.
- Added v1.5 release notes and plain-text changelog archive entry.

### Release Notes

- Package version is `1.5.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- SQLite remains the only required memory backend.
- Qdrant remains optional for RAG context and reviews still fall back to diff-only mode when unavailable.
- Optional knowledge connectors are design-time extension points only in this release and add no mandatory external services.
- PyPI publishing requires a `PYPI_TOKEN` repository secret.

## v1.4.0 - 2026-07-08

OpenRabbit v1.4.0 strengthens repository context, automation safety, provider diagnostics, PR exploration output, and release-quality evaluation evidence.

### Context Intelligence

- Improved RAG retrieval planning with changed-file, changed-symbol, directory, guideline, and semantic retrieval reasons.
- Added deterministic context packing so review prompts prefer directly relevant changed files, scoped guidelines, and nearby code context.
- Added context-source reasons to verbose review output so users can see why indexed files were included.

### Automation

- Added polling controls for bounded concurrent reviews, review cooldowns, and changed-file skip thresholds.
- Added structured daemon logs for review started, skipped, completed, and failed events.
- Hardened the GitHub Actions self-hosted runner recipe with dry-run defaults, Qdrant health checks, pinned release refs, and troubleshooting notes.

### Model Providers

- Added `openrabbit model-health` to verify Ollama, official OpenAI, and OpenAI-compatible providers before running a full PR review.
- Added provider setup documentation for health checks, missing API keys, invalid base URLs, stopped local servers, and empty model responses.
- Clarified that future local serving adapters belong behind extension points, while current runtime providers use the shared LLM client factory.

### PR Exploration

- Added `--format text|markdown|json` to `openrabbit describe`.
- Added `--format text|markdown|json` to `openrabbit ask`.
- Added deterministic JSON output for scripts and Markdown output for reports or future safe publish flows.

### Evaluation

- Added `openrabbit eval --compare` to compare current PR regression logs against a previous JSON report.
- Added `openrabbit eval --expectations` for expected min/max finding counts and category assertions.
- Added JSON and Markdown report sections for trend deltas and assertion results.

### Documentation

- Updated README coverage for provider health checks, structured describe/ask output, GitHub Actions, and eval comparison/assertions.
- Added v1.4 release notes and plain-text changelog archive entry.
- Updated PR-Agent gap analysis and GitHub Actions examples for the v1.4 release.

### Release Notes

- Package version is `1.4.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- SQLite remains the only required memory backend.
- Qdrant remains optional for RAG context and reviews still fall back to diff-only mode when unavailable.
- PyPI publishing requires a `PYPI_TOKEN` repository secret.

## v1.3.0 - 2026-07-08

OpenRabbit v1.3.0 adds local memory maintenance and the first CodeRabbit-style knowledge sources while keeping the platform local-first and service-light.

### Memory Maintenance

- Added memory export for deterministic, secret-safe repository memory snapshots.
- Added memory pruning by date for local SQLite review runs and findings.
- Added `openrabbit memory --learnings` to inspect active local repository learnings.
- Added explicit `/openrabbit learn ...` support in polling mode so maintainers can store durable review instructions.

### Memory Backend Design

- Added memory backend extension documentation for future graph and vector memory plugins.
- Kept SQLite as the only required runtime memory backend.
- Documented adapter boundaries so graph/vector stores can enrich memory later without becoming mandatory services.

### Repository Knowledge

- Added automatic guideline detection for `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`, `.windsurfrules`, and `.rules/**`.
- Added path-local guideline scope metadata so prompt context can show where repository rules apply.
- Preserved `.openrabbit/*` rules and legacy `.codereviewer/*` rule indexing.
- Added prompt labels and provenance metadata for repository guideline context.

### Linked Issue Context

- Added GitHub linked issue parsing from PR title, PR body, commit messages, and head branch metadata.
- Added compact linked issue context with title, state, labels, URL, and body preview.
- Included linked issue context in review, describe, ask, and improve prompts.
- Continued reviews when linked issue lookup fails, logging a warning instead of failing PR parsing.

### Evaluation

- Extended `openrabbit eval` JSON and Markdown reports with v1.3 context fields: memory context, active learning count, guideline sources, and linked issue count.
- Added totals for learnings, linked issues, and unique guideline sources.
- Added regression coverage for memory maintenance, learnings, guideline detection, linked issue context, and eval context fields.

### Documentation

- Added repository guideline documentation.
- Added memory backend design documentation.
- Updated README coverage for local learnings, guideline detection, linked issue context, and v1.3 eval fields.
- Added v1.3 release notes and plain-text changelog archive entry.

### Release Notes

- Package version is `1.3.0`.
- Python support remains `>=3.12,<3.14`.
- The default model provider remains Ollama.
- SQLite remains the only required memory backend.
- Graph/vector memory, MCP, web search, Jira/Linear, SAST, dashboards, and autofix remain planned for later phases.
- PyPI publishing requires a `PYPI_TOKEN` repository secret.

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
- Added PR comment command handling in polling mode for `/openrabbit review`, `full review`, `improve`, `ask`, `pause`, and `resume`.
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
