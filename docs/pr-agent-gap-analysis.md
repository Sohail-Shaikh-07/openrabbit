# OpenRabbit vs PR-Agent Gap Analysis

Date: 2026-07-02

This document compares the current OpenRabbit `v1.0.0` implementation with The-PR-Agent/pr-agent and turns the gap into a practical roadmap.

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
| Config | `.openrabbit/config.yml` plus `OPENRABBIT_...` env overrides |
| Manual review | `openrabbit review --pr N --repo owner/repo` fetches and parses PR data, runs agents, ranks findings, prints a summary, and publishes grounded findings when not in dry-run mode |
| Polling | `openrabbit start` watches a repository and records polling state |
| Publishing | Manual review publishing is wired; polling is not yet wired to execute and publish reviews automatically |
| Local model | Ollama provider is wired; vLLM and Transformers are schema placeholders |
| Agents | Security, performance, architecture, bug, and test coverage agents |
| Review quality controls | Changed-line evidence, JSON-only prompt contract, grounding to changed files/lines, duplicate removal |
| RAG | Scanner, chunker, embeddings, Qdrant vector store, and indexing command |
| Fine-tuning | QLoRA training/evaluation/packaging pipeline for a Qwen2.5-Coder adapter |
| Benchmarks | Runner, scorer, and profiler for review quality evaluation |

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

### 1. Automated review publishing is not wired end to end

OpenRabbit has manual review publishing, but `openrabbit start` still logs polling events without invoking the full review pipeline.

Recommended tasks:

- Wire polling events from `openrabbit start` into review execution and publishing.
- Add tests for dry-run/no-post, publish-on-review, and polling-to-review behavior.

### 2. No PR description or walkthrough command

PR-Agent's `/describe` generates a PR summary, title, labels, and walkthrough. OpenRabbit currently focuses on findings only.

Recommended tasks:

- Add `openrabbit describe --pr N`.
- Generate a concise PR summary, changed-file walkthrough, risk areas, and suggested test focus.
- Optionally publish or update a PR comment/body when enabled.

### 3. No improvement or fix-suggestion command

PR-Agent's `/improve` proposes code improvements. OpenRabbit findings include optional `fix` snippets, but there is no dedicated improve workflow.

Recommended tasks:

- Add `openrabbit improve --pr N`.
- Return patch-style suggestions only when grounded to changed lines.
- Keep suggestions small and reviewable.

### 4. No interactive ask command

PR-Agent supports `/ask` and line-level questions. OpenRabbit has no way to ask the local model a question about a PR.

Recommended tasks:

- Add `openrabbit ask --pr N "question"`.
- Retrieve PR diff plus repository context before answering.
- Add a strict answer contract that separates evidence, answer, and uncertainty.

### 5. Large PR compression is missing

OpenRabbit formats changed-line evidence and limits prompt context, but it does not yet have PR-Agent-style adaptive patch fitting or compression for large PRs.

Recommended tasks:

- Add a token budget estimator for changed files, hunks, and retrieved context.
- Prioritize changed lines, nearby context, security-sensitive files, and directly related RAG hits.
- Summarize low-risk or oversized files before agent execution.
- Report what was omitted from the model context.

### 6. RAG is indexed but not yet clearly integrated into every review path

The RAG indexer and vector store exist, but the manual review command currently passes no retrieval result into the agent pipeline. That means reviews can still behave as diff-only unless a caller wires retrieval manually.

Recommended tasks:

- Load retrieval context automatically in `openrabbit review`.
- Query by changed files, changed symbols, and PR title/body.
- Feed coding rules, security rules, architecture notes, and related source chunks to the relevant agents.

### 7. Provider abstraction is incomplete

The config schema allows `ollama`, `vllm`, and `transformers`, but only Ollama is wired in `build_review_agents`.

Recommended tasks:

- Define a common LLM client protocol.
- Implement vLLM and Transformers clients or remove them from the public schema until they are ready.
- Add provider-specific health checks and error messages.

### 8. Config layering is basic

OpenRabbit has local YAML plus env overrides. PR-Agent supports multiple persistent config locations with clear precedence.

Recommended tasks:

- Add user-level config, for example `~/.openrabbit/config.yml`.
- Add organization or repository-default config support.
- Document precedence: environment > repo config > user config > defaults.
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
| P0 | Wire review publishing and polling-to-review | Makes OpenRabbit behave like a real PR reviewer, not only a preview CLI |
| P0 | Auto-load RAG context during review | Delivers the repository-aware promise |
| P1 | Add PR description command | Fast, visible value for every PR |
| P1 | Add token-aware PR compression | Required for large real-world PRs |
| P1 | Add improve/fix suggestions | Moves from finding problems to helping resolve them |
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
