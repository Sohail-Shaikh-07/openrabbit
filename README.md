# OpenRabbit

OpenRabbit is a local-first AI pull request reviewer for GitHub repositories. You run it on your own machine, point it at a repo, and use a local model to inspect pull request diffs with repository context.

The core trade-off is privacy and ownership: source code is reviewed on your laptop or server, with Ollama as the default model runtime and Qdrant as the repository context store.

## What Works Today

| Area | Current capability |
| --- | --- |
| CLI | `init`, `index`, `review`, `describe`, `ask`, `improve`, `eval`, `start`, `install-model`, `--quiet`, `--verbose`, `--version` |
| Configuration | Built-in defaults, `~/.openrabbit/config.yml`, repo `.openrabbit/config.yml`, `OPENRABBIT_...` environment overrides, Windows persistent env fallback for GitHub tokens |
| GitHub | PAT auth, repository handles, PR metadata, commits, changed files, hunks, binary-file handling |
| Model layer | Shared provider contract for Ollama, official OpenAI, and OpenAI-compatible chat completions endpoints |
| Agents | Security, performance, architecture, bug, and test coverage agents |
| Prompting | Changed-line evidence first, token-aware PR diff compression, strict JSON contract, no speculative findings |
| Ranking | Severity/confidence scoring, duplicate removal, changed-line grounding |
| PR memory | Local SQLite review memory, finding fingerprints, re-review status labels, PR conversation models |
| RAG | Repository scanner, chunker, embeddings, Qdrant vector store, indexing CLI, automatic review context loading |
| Fine-tuning | QLoRA training, dataset cleaning/formatting, evaluation, adapter packaging |
| Benchmarks | Benchmark runner, scorer, profiler, and packaged v1.1 regression corpus |

## Requirements

- Python 3.12 or 3.13
- Poetry, or `pip` for a local package install
- GitHub personal access token with access to the target repository
- Ollama for local review inference
- Qdrant for repository indexing
- Docker, optional, for running Qdrant locally
- OpenAI or OpenAI-compatible API key, optional, only when using hosted or gateway providers

## Install

From the OpenRabbit repository:

```bash
poetry install
poetry run openrabbit --help
```

Or install into your user Python environment:

```bash
python -m pip install --user .
openrabbit --help
```

If the `openrabbit` command is not found after `pip install --user`, make sure your Python user scripts directory is on `PATH`.

## Model Setup

See [docs/model-providers.md](docs/model-providers.md) for the full provider setup guide, secret handling rules, environment overrides, and troubleshooting.

OpenRabbit uses Ollama by default. For a base-model test before fine-tuning:

```bash
ollama pull qwen2.5-coder:7b
ollama run qwen2.5-coder:7b
```

Then set `.openrabbit/config.yml` to:

```yaml
model:
  provider: ollama
  model_name: qwen2.5-coder:7b
  base_model: qwen2.5-coder:7b
```

When you have packaged a fine-tuned adapter as an Ollama model, switch `model_name` to that local model name:

```yaml
model:
  provider: ollama
  model_name: openrabbit-reviewer-v1
  base_model: qwen2.5-coder:7b
```

See [docs/model-finetuning.md](docs/model-finetuning.md) for the Colab training flow, Hugging Face adapter packaging, Ollama import, and local configuration.

### OpenAI Provider

If you want to use the official OpenAI API instead of local Ollama, set the API key in your shell and switch the provider:

PowerShell:

```powershell
setx OPENAI_API_KEY "sk_your_key_here"
```

macOS/Linux:

```bash
export OPENAI_API_KEY="sk_your_key_here"
```

`.openrabbit/config.yml`:

```yaml
model:
  provider: openai
  model_name: gpt-4.1-mini
  api_key_env: OPENAI_API_KEY
```

Do not put the API key value in `.openrabbit/config.yml`. OpenRabbit rejects inline model secrets such as `model.api_key`; it reads the variable named by `model.api_key_env` and sends it only in the provider request header. For custom endpoint roots, use the OpenAI-compatible provider below. More detail is in [docs/model-providers.md](docs/model-providers.md).

### OpenAI-Compatible Provider

Use `openai-compatible` for gateways that expose a `/v1/chat/completions` API, such as vLLM OpenAI server, LiteLLM, OpenRouter-style endpoints, local gateways, or enterprise model gateways.

PowerShell:

```powershell
setx OPENAI_COMPATIBLE_API_KEY "your_gateway_key_here"
```

macOS/Linux:

```bash
export OPENAI_COMPATIBLE_API_KEY="your_gateway_key_here"
```

`.openrabbit/config.yml`:

```yaml
model:
  provider: openai-compatible
  model_name: openai/gpt-oss-20b
  base_url: http://localhost:8000/v1
  api_key_env: OPENAI_COMPATIBLE_API_KEY
```

`base_url` should be the endpoint root, without `/chat/completions`. For local servers that do not enforce authentication, set the configured environment variable to a harmless placeholder such as `local-key`; OpenRabbit still sends it only in the request header. See [docs/model-providers.md](docs/model-providers.md) for examples and troubleshooting.

## GitHub Token Setup

OpenRabbit reads the token in this order:

1. `OPENRABBIT_GITHUB__TOKEN`
2. The environment variable named by `github.token_env` in `.openrabbit/config.yml`, default `GITHUB_TOKEN`
3. On Windows, persistent User or Machine environment variables when the current shell has stale env state

PowerShell:

```powershell
setx GITHUB_TOKEN "github_pat_your_token_here"
```

Open a new terminal after `setx`. OpenRabbit also checks persistent Windows User and Machine environment variables, so the token can still be found even when the current shell has stale environment state.

macOS/Linux:

```bash
export GITHUB_TOKEN="github_pat_your_token_here"
```

## Repository Setup

Inside the repository you want OpenRabbit to review:

```bash
openrabbit init
```

This creates:

```text
.openrabbit/
  config.yml
  architecture.md
  coding_rules.md
  security_rules.md
  review_examples.md
  ignore.txt
  .gitignore
```

A minimal config:

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
  max_files: 80
  max_changed_lines: 4000
  include_generated: false

model:
  provider: ollama
  model_name: qwen2.5-coder:7b
  base_model: qwen2.5-coder:7b

polling:
  interval_seconds: 60

github:
  token_env: GITHUB_TOKEN

repository:
  target: owner/repo

memory:
  enabled: true
  # Local SQLite memory is stored under .openrabbit/state by default.
  # path: state/openrabbit.db
```

For the official OpenAI API, use `provider: openai` and put the API model in `model_name`. You do not need `base_model` for API providers:

```yaml
model:
  provider: openai
  model_name: gpt-4.1-mini
  api_key_env: OPENAI_API_KEY
```

For a custom OpenAI-compatible endpoint, set `provider` to the provider name you want OpenRabbit to show, set the served model in `model_name`, and set the endpoint root in `base_url`. For example, OpenRouter:

```yaml
model:
  provider: openrouter
  model_name: openai/gpt-oss-20b
  base_url: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY
```

The generic name `openai-compatible` still works, but a concrete name such as `openrouter`, `vllm`, or `litellm` is clearer in logs and diagnostics. Any provider other than `ollama` or official `openai` is treated as OpenAI-compatible when `base_url` is set.

`base_model` is mainly useful as local-model/fine-tuning metadata for Ollama and adapter workflows. It is not sent to OpenAI or OpenAI-compatible API providers during review.

Review controls let each repository tune how OpenRabbit behaves. Use `profile: chill` for quieter high-confidence reviews, or `profile: assertive` for broader concrete risk coverage. `path_include` and `path_exclude` accept glob patterns, `path_instructions` adds targeted guidance for matching paths, and the max-file/max-line/generated controls prevent large or generated changes from overwhelming prompts. When paths are skipped, `openrabbit review` reports them in the CLI summary.

Example path-specific guidance:

```yaml
review:
  path_instructions:
    - path: "app/api/**"
      instructions: "Require explicit authorization checks before mutations."
```

OpenRabbit loads configuration in layers:

1. Built-in defaults
2. User defaults from `~/.openrabbit/config.yml`, when present
3. Repository config from `.openrabbit/config.yml` or legacy `.codereviewer/config.yml`
4. `OPENRABBIT_...` environment overrides

Use the user config for repeated local defaults such as model provider, model name, polling interval, or `github.token_env`. Keep repository-specific review rules in the repo config. Do not store token values or model API keys in either config file; store secrets in environment variables and reference their names.

OpenRabbit stores local PR memory in `.openrabbit/state/openrabbit.db` by default. This memory helps identify whether findings are new, still present, or possibly fixed across re-runs. `openrabbit init` writes `.openrabbit/.gitignore` so local state, cache, memory folders, and SQLite databases are not committed. See [docs/pr-memory.md](docs/pr-memory.md). Future graph and vector memory plugins are planned as optional local-first adapters, documented in [docs/memory-backends.md](docs/memory-backends.md).

Any config value can be overridden with an `OPENRABBIT_` environment variable using `__` between nested fields:

```bash
OPENRABBIT_POLLING__INTERVAL_SECONDS=30
OPENRABBIT_REVIEW__STYLE=true
OPENRABBIT_GITHUB__TOKEN=github_pat_your_token_here
```

## CLI Commands

### `openrabbit init`

Creates the `.openrabbit/` scaffold.

```bash
openrabbit init
openrabbit init --path /path/to/repo
openrabbit init --force
```

### `openrabbit index`

Scans a repository, chunks docs/source/rules, embeds them, and stores them in Qdrant.

```bash
docker compose up -d qdrant
openrabbit index --workspace . --health
openrabbit index --workspace . --qdrant-host localhost --qdrant-port 6333
```

Run this after `openrabbit init`, after major documentation or architecture changes, and after large source changes when you want reviews to use fresh repository context. If Qdrant is unavailable or no index exists, reviews continue in diff-only mode and report `Context: diff only`.

The index includes source symbols, tests, documentation, README-style files, prior review examples, and `.openrabbit/*` rules. During review, OpenRabbit uses the changed file paths and changed symbols from the PR diff to prefer context from the files being edited, while still allowing architecture docs and review rules to contribute broader guidance.

Use `openrabbit index --health` to confirm Qdrant is reachable and list the available collections before reviewing. When repository context is loaded, `openrabbit review` prints compact context provenance under `Context sources:` so you can see which indexed files influenced the run.

The embedding model is downloaded once by FastEmbed when indexing or real RAG retrieval first needs it. OpenRabbit checks Qdrant for an existing RAG index before loading embeddings during review, so a machine without Qdrant or without an index should not trigger an embedding download just to fall back to diff-only mode.

### `openrabbit review`

Fetches one PR, loads indexed repository context when available, runs the enabled local agents, grounds findings to the diff, prints a ranked summary, and publishes a GitHub review when findings exist.

```bash
openrabbit review --pr 42 --repo owner/repo --dry-run
openrabbit review --pr 42 --repo owner/repo
openrabbit review --pr 42 --repo owner/repo --mode full
openrabbit review --pr 42 --repo owner/repo --mode incremental
openrabbit --quiet review --pr 42 --repo owner/repo --dry-run
openrabbit --verbose review --pr 42 --repo owner/repo --dry-run
```

Use `--dry-run` to print the result locally without posting comments. Empty findings are not posted, so clean PRs do not receive noisy review comments.

Each review records local structured memory when `memory.enabled` is true. The summary includes memory state, and each finding can be tagged as `new` or `still_present` based on prior OpenRabbit runs for the same PR.

`--mode incremental` is the default. It publishes only new findings and suppresses repeat comments for findings that are still present from an earlier OpenRabbit run. `--mode full` reruns and republishes all grounded findings, which is useful when you intentionally want a fresh full review.

Review agents receive changed-line evidence before the full diff. For larger pull requests, OpenRabbit rebuilds a compact diff from parsed GitHub hunks, prioritizes risky and code-heavy files, keeps prompts within a deterministic token budget, and includes an omission note when content is left out.

Today, `model.provider: ollama`, `model.provider: openai`, and `model.provider: openai-compatible` are implemented. The model layer uses a shared provider contract so more runtimes can plug into the same review-agent pipeline.

### `openrabbit memory`

Inspects the local SQLite PR memory for a repository pull request. This command is read-only: it does not fetch GitHub data, call a model, create a database, or post anything to the pull request.

```bash
openrabbit memory --pr 42 --repo owner/repo
openrabbit memory --pr 42 --repo owner/repo --format json
openrabbit memory --workspace /path/to/repo --pr 42 --repo owner/repo
openrabbit memory --repo owner/repo --export .openrabbit/reports/memory.json
openrabbit memory --repo owner/repo --prune-before 2026-01-01
```

Use this after one or more reviews to see the configured memory path, last reviewed SHA, finding status counts, and stored finding fingerprints. Export writes deterministic secret-safe JSON for local debugging or migration. Prune deletes local memory rows older than the given date.

### `openrabbit describe`

Fetches one PR, loads indexed repository context and local PR memory when available, and prints a read-only summary, changed-file walkthrough, risk areas, and testing focus. It uses the same configured model provider as `openrabbit review`, but it never publishes comments or mutates the pull request.

```bash
openrabbit describe --pr 42 --repo owner/repo
openrabbit --quiet describe --pr 42 --repo owner/repo
```

### `openrabbit ask`

Fetches one PR, loads indexed repository context and local PR memory when available, and answers a focused question about the pull request. The answer is separated into direct answer, evidence, uncertainty, and follow-up checks. The command uses the same configured model provider as `openrabbit review`, but it never posts comments or mutates the pull request.

```bash
openrabbit ask --pr 42 --repo owner/repo "Does this change add enough test coverage?"
openrabbit --quiet ask --pr 42 --repo owner/repo "What files should I inspect first?"
```

### `openrabbit improve`

Fetches one PR, loads indexed repository context and local PR memory when available, and prints improvement suggestions for changed lines. Suggestions are grounded to changed files and changed new-side lines before they are shown. The command uses the same configured model provider as `openrabbit review`, but it never applies patches or pushes commits.

By default, `improve` is read-only. Add `--publish` only when you want OpenRabbit to post grounded, actionable suggestions to the pull request. Suggestions with concrete replacement snippets become GitHub suggestion blocks on changed lines. Broader actionable suggestions are grouped into the review body, and non-actionable TODO/comment-only advice is dropped.

```bash
openrabbit improve --pr 42 --repo owner/repo
openrabbit improve --pr 42 --repo owner/repo --dry-run
openrabbit improve --pr 42 --repo owner/repo --publish
openrabbit --quiet improve --pr 42 --repo owner/repo
```

### `openrabbit eval`

Runs repeatable dry-run reviews over selected pull requests and writes a structured JSON test log plus a Markdown dashboard. By default it evaluates PRs `1,2,3,4,5`, which is the first OpenRabbit regression scenario set used for `testing-openrabbit`.

```bash
openrabbit eval --repo owner/repo
openrabbit eval --repo owner/repo --prs 1,2,3,4,5
openrabbit eval --repo owner/repo --output .openrabbit/reports/review-eval.json
```

Each run captures the command, PR number, provider, model, context mode, finding count, finding categories, dropped findings, skipped paths, runtime, and failure text when a PR run fails.

### `openrabbit start`

Runs the polling service in the foreground, records PR polling state under `.openrabbit/state.json`, and reviews new PRs or new head commits automatically.

```bash
openrabbit start --workspace . --repo owner/repo
```

The first poll seeds state without reviewing every already-open PR. After that, new PRs and changed head SHAs trigger the same review-and-publish path as `openrabbit review`. Same-SHA updates, such as label or description changes, are logged and skipped.

While `openrabbit start` is running, OpenRabbit also listens for new PR comments addressed to it:

```text
@openrabbit review
@openrabbit full review
@openrabbit improve
@openrabbit ask what changed in the search path?
@openrabbit pause
@openrabbit resume
```

Comment commands are only handled by the polling service. They are not active during one-off CLI commands. Pause state and the last processed comment cursor are stored locally under `.openrabbit/commands.json`; paused PRs skip automatic review until `@openrabbit resume` is received.

### `openrabbit install-model`

Downloads a PEFT/LoRA adapter package from Hugging Face into `~/.openrabbit/models/`.

```bash
openrabbit install-model
openrabbit install-model --model-id myorg/my-adapter --token hf_your_token_here
```

This installs the adapter files. To use the adapter with Ollama, create an Ollama model from the adapter as described in [docs/model-finetuning.md](docs/model-finetuning.md).

## Docker Notes

`docker-compose.yml` is useful for starting Qdrant during local development:

```bash
docker compose up -d qdrant
```

If Docker is not installed, `openrabbit review`, `openrabbit describe`, `openrabbit ask`, and `openrabbit improve` still work from the PR diff. Install/start Qdrant and run `openrabbit index` when you want repository-aware RAG context.

Copy `.env.example` to `.env` if you want compose to pass a GitHub token or a custom workspace path into the containerized CLI. The `openrabbit` image packages the CLI, but local source install is still the most direct workflow for reviewing a working repository because `openrabbit init` needs write access to create `.openrabbit/`.

## GitHub Actions

Use [docs/github-actions.md](docs/github-actions.md) for a self-hosted or configured-runner workflow recipe. A copyable example lives at [examples/github-actions/openrabbit-review.yml](examples/github-actions/openrabbit-review.yml).

## Repository Layout

```text
src/
  cli/           Typer entry point and subcommands
  configs/       YAML and environment configuration
  github_/       GitHub REST client, PR parser, polling, publisher
  memory/        Local PR memory, fingerprints, history formatting
  rag/           Repository scanner, chunker, embeddings, Qdrant store
  agents/        Local multi-agent review pipeline
  ranking/       Changed-line grounding, deduplication, scoring
  finetuning/    QLoRA training, evaluation, adapter packaging
  benchmarks/    Review quality benchmark runner, scorer, profiler
  api/           Placeholder local API package
tests/
scripts/
  train.py        Run a QLoRA fine-tuning job
  smoke_test.py   Cross-platform install verification
```

## Benchmarks

```python
from benchmarks import BenchmarkCase, BenchmarkRunner, BenchmarkScorer, load_benchmark_cases

cases = [
    BenchmarkCase(
        case_id="example-001",
        diff="...",
        known_issues=["SQL injection in login handler"],
    )
]

# Or use the packaged v1.1 regression corpus:
cases = load_benchmark_cases()

runner = BenchmarkRunner(agents=[...])
report = await runner.run(cases)

scorer = BenchmarkScorer()
scored = scorer.score(report, cases)
print(f"macro F1: {scored.macro_f1:.3f}")
```

See [docs/benchmark-corpus.md](docs/benchmark-corpus.md) for the packaged corpus format and regression coverage.

## Development

```bash
poetry install
poetry run pytest
poetry run ruff check .
poetry run black --check .
poetry run mypy
poetry run python scripts/smoke_test.py
```

## License

Apache 2.0.
