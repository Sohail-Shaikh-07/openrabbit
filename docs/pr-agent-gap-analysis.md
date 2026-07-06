# OpenRabbit vs PR-Agent Gap Analysis

Date: 2026-07-06

This document compares the current OpenRabbit `v1.1.0` implementation with The-PR-Agent/pr-agent and turns the remaining gap into a practical roadmap.

Sources reviewed:

- PR-Agent repository: https://github.com/The-PR-Agent/pr-agent
- PR-Agent tools documentation: https://docs.pr-agent.ai/tools/
- PR-Agent configuration documentation: https://docs.pr-agent.ai/usage-guide/configuration_options/
- PR-Agent core abilities documentation: https://docs.pr-agent.ai/core-abilities/
- PR-Agent automations and usage documentation: https://docs.pr-agent.ai/usage-guide/automations_and_usage/

## Positioning

OpenRabbit and PR-Agent make different default trade-offs.

OpenRabbit is designed to be local-first: GitHub metadata is fetched from GitHub, but code review inference runs locally through Ollama. The long-term product direction is a self-hosted reviewer with repository-aware RAG, multi-agent checks, optional fine-tuned local models, and no dependency on a hosted LLM API.

PR-Agent is a mature automation-first reviewer. It supports many hosting modes, many Git platforms, and a broad command set around reviewing, describing, improving, asking questions, labels, docs, changelogs, and help.

## Current OpenRabbit Capabilities

| Capability | Current state |
| --- | --- |
| GitHub auth | Personal access token from `OPENRABBIT_GITHUB__TOKEN`, `GITHUB_TOKEN`, configured `token_env`, or Windows persistent env fallback |
| Config | Built-in defaults, optional `~/.openrabbit/config.yml`, repo config, and `OPENRABBIT_...` env overrides |
| Manual review | `openrabbit review --pr N --repo owner/repo` fetches and parses PR data, runs agents, ranks findings, prints a summary, and publishes grounded findings when not in dry-run mode |
| Polling | `openrabbit start` watches a repository, records polling state, and triggers reviews for new PRs and new head SHAs |
| Publishing | Manual review publishing and polling-triggered publishing are wired for GitHub |
| Model layer | Shared provider contract exists; Ollama, official OpenAI, and OpenAI-compatible base URL providers are wired; vLLM and Transformers remain placeholders |
| Agents | Security, performance, architecture, bug, and test coverage agents |
| Review quality controls | Changed-line evidence, JSON-only prompt contract, grounding to changed files/lines, duplicate removal |
| RAG | Scanner, chunker, embeddings, Qdrant vector store, indexing command, and automatic review context loading |
| Fine-tuning | QLoRA training/evaluation/packaging pipeline for a Qwen2.5-Coder adapter |
| Benchmarks | Runner, scorer, profiler, and packaged v1.1 regression corpus for review quality evaluation |

## PR-Agent Capabilities To Learn From

PR-Agent's public docs describe a much wider command and automation surface:

| PR-Agent area | Examples |
| --- | --- |
| Review tools | `/review`, `/improve`, `/describe`, `/ask`, ask on code lines |
| Repo maintenance tools | `/add_docs`, `/generate_labels`, `/similar_issue`, `/update_changelog`, `/help_docs` |
| Deployment modes | CLI, GitHub Action, app/webhook flow, Docker, self-hosted server |
| Git providers | GitHub, GitLab, Bitbucket, Azure DevOps, Gitea |
| Model providers | Multiple hosted and self-hosted LLM providers |
| Large PR handling | Adaptive/token-aware file patch fitting and PR compression |
| Context | Dynamic context, ticket context, local/global metadata |
| Interaction | Commands in PR comments and line-level conversations |
| Config | Wiki, local config, global config, external config URL, non-default branch config |
| Quality loop | Self-reflection and configurable prompt behavior |

## High-Priority Gaps

### 1. Review publishing needs operational hardening

OpenRabbit now publishes manual reviews and polling-triggered reviews, but the automation path still needs operational polish before it reaches PR-Agent maturity.

Recommended tasks:

- Add controls for review frequency, max PR size, and per-repo concurrency.
- Add clearer daemon observability around skipped PRs, posted comments, and provider failures.
- Add webhook or GitHub Action modes for teams that do not want a long-running local polling process.

### 2. PR description and walkthrough command now has a first pass

PR-Agent's `/describe` generates a PR summary, title, labels, and walkthrough. OpenRabbit now has a read-only `openrabbit describe` command that prints a PR summary, changed-file walkthrough, risk areas, and testing focus through the configured model provider.

Recommended tasks:

- Add optional markdown or JSON output formats.
- Optionally publish or update a PR comment/body when enabled.

### 3. Improvement and fix-suggestion command now has a first pass

PR-Agent's `/improve` proposes code improvements. OpenRabbit now has `openrabbit improve`, which proposes small fixes for changed lines, grounds suggestions to changed files and changed new-side lines, and stays read-only unless `--publish` is passed.

Recommended tasks:

- Add quality benchmarks for over-suggestion and false-positive rates.
- Expand suggestion quality checks beyond the current grounded/actionable filters.

### 4. Interactive ask command now has a first pass

PR-Agent supports `/ask` and line-level questions. OpenRabbit now has a read-only `openrabbit ask` command that answers a focused question about a PR using metadata, changed-line evidence, diff context, and retrieved repository context when available.

Recommended tasks:

- Add optional JSON output for scripting.
- Add optional PR comment publishing when explicitly enabled.
- Add line-level ask support for a selected file and line.

### 5. Large PR compression and review controls now have a first pass

OpenRabbit formats changed-line evidence, rebuilds prompt diffs from parsed GitHub hunks, prioritizes risky and code-heavy files, and includes omission notes when a PR exceeds the prompt budget.

OpenRabbit now also supports repository-level review controls: `chill` and `assertive` profiles, include/exclude path globs, path-specific review instructions, max-file and max-changed-line limits, generated-file defaults, and skipped-path reporting in the review summary.

Recommended tasks:

- Tune token budgets per review agent.
- Summarize low-risk or oversized files before agent execution.
- Expand controls into organization-level defaults once multi-repo config is available.

### 6. RAG needs deeper and more selective context packing

The review command now loads repository context automatically when Qdrant has an index available. The next gap is making context selection more precise for large repositories and large pull requests.

Recommended tasks:

- Query by changed symbols and related call sites, not only PR metadata and hunk text.
- Coordinate context packing with the token-aware compression task.
- Surface which context sources were used in verbose mode.

### 7. Provider breadth still needs local-runtime cleanup

The review-agent pipeline now uses a shared provider contract. Ollama, the official OpenAI API, and custom OpenAI-compatible base URLs are wired through the provider factory. The public schema still lists `vllm` and `transformers` as placeholders.

Recommended tasks:

- Decide whether `vllm` and `transformers` should be implemented soon or removed from the public schema until they are ready.
- Add provider-specific health checks and error messages.

### 8. Config layering now has a first pass

OpenRabbit now supports built-in defaults, optional user-level config at `~/.openrabbit/config.yml`, repository config, and environment overrides with clear precedence. PR-Agent still has broader organization/global config patterns.

Recommended tasks:

- Add organization or repository-default config support.
- Consider an external config URL only with strict size, timeout, and scheme restrictions.

### 9. No GitHub Action or webhook entrypoint

OpenRabbit is local-first, but users still need an easy automation path. PR-Agent's GitHub Action flow is a major adoption advantage.

Recommended tasks:

- Add a GitHub Action recipe for self-hosted runners.
- Add webhook server mode for users who want push-based review.
- Keep local Ollama/Qdrant dependencies explicit.

### 10. Missing repo-maintenance tools

OpenRabbit does not yet provide equivalents for labels, changelogs, docs generation, similar issue search, or help docs.

Recommended tasks:

- Add labels only after PR summary quality is stable.
- Add changelog updates for release PRs.
- Add docs generation for changed public functions/classes.
- Add similar issue lookup after GitHub issue search is available.

## Recommended Roadmap

| Priority | Task | Why |
| --- | --- | --- |
| P0 | Improve RAG context selection and packing | Keeps repository-aware reviews precise as PRs and repos grow |
| Done | Harden review automation controls | Prevents noisy daemon behavior on large or busy repositories |
| Done | Add PR description command | Fast, visible value for every PR |
| Done | Add token-aware PR compression | Keeps large real-world PRs inside deterministic prompt budgets |
| Done | Add improve/fix suggestions | Moves from finding problems to helping resolve them |
| P1 | Add GitHub Action recipe | Removes local manual friction for teams |
| P2 | Add ask command | Useful for interactive PR exploration |
| P2 | Expand provider support | Helps teams use their preferred local or hosted runtime |
| P2 | Add layered config | Important for teams and repeated use |
| P3 | Labels, changelog, docs, similar issues | Valuable after the core review loop is reliable |

## OpenRabbit Differentiators To Preserve

- Local-first inference by default.
- No requirement for OpenAI, Anthropic, or other hosted model APIs.
- Repository-aware review using local project docs and rules.
- Multi-agent review categories with explicit enable/disable settings.
- Fine-tuning path for an OpenRabbit-specific reviewer model.
- Strict grounding so findings stay tied to changed files and lines.

The path forward is not to clone every PR-Agent feature immediately. The strongest next move is to make the core review loop fully end to end, then add the highest-value automation and interaction commands while preserving local-first privacy.
