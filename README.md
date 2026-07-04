# OpenRabbit

OpenRabbit is a local-first AI pull request reviewer for GitHub repositories. You run it on your own machine, point it at a repo, and use a local model to inspect pull request diffs with repository context.

The core trade-off is privacy and ownership: source code is reviewed on your laptop or server, with Ollama as the default model runtime and Qdrant as the repository context store.

## Current Status

OpenRabbit is at `v1.0.0`. The foundation is in place: CLI, configuration, GitHub PR parsing, repository indexing, local multi-agent review, ranking, fine-tuning utilities, and release validation.

The current manual review flow is:

1. OpenRabbit fetches a pull request from GitHub.
2. It parses commits, changed files, hunks, and changed-line evidence.
3. It tries to retrieve relevant repository context from Qdrant using the PR title, body, changed files, and hunk lines.
4. Enabled review agents run against a token-aware compressed diff plus any retrieved context with strict JSON output prompts.
5. Findings are grounded to changed files and changed lines.
6. A ranker removes duplicates, orders the findings, and drops ungrounded output.
7. The CLI prints the summary locally.
8. If `--dry-run` is not set and findings exist, OpenRabbit posts them as a GitHub review.

`openrabbit start` runs the polling daemon and reviews new pull requests or new head commits automatically. Metadata-only PR updates with the same head SHA are skipped to avoid repeated reviews. Use `openrabbit review --dry-run` as the safe manual preview path. See [docs/pr-agent-gap-analysis.md](docs/pr-agent-gap-analysis.md) for the current comparison against PR-Agent and the recommended roadmap.

## What Works Today

| Area | Current capability |
| --- | --- |
| CLI | `init`, `index`, `review`, `describe`, `improve`, `start`, `install-model`, `--quiet`, `--verbose`, `--version` |
| Configuration | `.openrabbit/config.yml`, `OPENRABBIT_...` environment overrides, Windows persistent env fallback for GitHub tokens |
| GitHub | PAT auth, repository handles, PR metadata, commits, changed files, hunks, binary-file handling |
| Model layer | Shared provider contract for Ollama, official OpenAI, and OpenAI-compatible chat completions endpoints |
| Agents | Security, performance, architecture, bug, and test coverage agents |
| Prompting | Changed-line evidence first, token-aware PR diff compression, strict JSON contract, no speculative findings |
| Ranking | Severity/confidence scoring, duplicate removal, changed-line grounding |
| RAG | Repository scanner, chunker, embeddings, Qdrant vector store, indexing CLI, automatic review context loading |
| Fine-tuning | QLoRA training, dataset cleaning/formatting, evaluation, adapter packaging |
| Benchmarks | Benchmark runner, scorer, and profiler for measuring review quality |

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

Open a new terminal after `setx`, or use this for the current session:

```powershell
$env:GITHUB_TOKEN = [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")
```

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

model:
  provider: ollama
  model_name: qwen2.5-coder:7b
  base_model: qwen2.5-coder:7b
  # base_url is only required for provider: openai-compatible
  # base_url: http://localhost:8000/v1
  api_key_env: OPENAI_API_KEY

polling:
  interval_seconds: 60

github:
  token_env: GITHUB_TOKEN

repository:
  target: owner/repo
```

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
openrabbit index --workspace . --qdrant-host localhost --qdrant-port 6333
```

Run this after `openrabbit init`, after major documentation or architecture changes, and after large source changes when you want reviews to use fresh repository context. If Qdrant is unavailable or no index exists, reviews continue in diff-only mode and report `Context: diff only`.

### `openrabbit review`

Fetches one PR, loads indexed repository context when available, runs the enabled local agents, grounds findings to the diff, prints a ranked summary, and publishes a GitHub review when findings exist.

```bash
openrabbit review --pr 42 --repo owner/repo --dry-run
openrabbit review --pr 42 --repo owner/repo
openrabbit --quiet review --pr 42 --repo owner/repo --dry-run
openrabbit --verbose review --pr 42 --repo owner/repo --dry-run
```

Use `--dry-run` to print the result locally without posting comments. Empty findings are not posted, so clean PRs do not receive noisy review comments.

Review agents receive changed-line evidence before the full diff. For larger pull requests, OpenRabbit rebuilds a compact diff from parsed GitHub hunks, prioritizes risky and code-heavy files, keeps prompts within a deterministic token budget, and includes an omission note when content is left out.

Today, `model.provider: ollama`, `model.provider: openai`, and `model.provider: openai-compatible` are implemented. The model layer uses a shared provider contract so more runtimes can plug into the same review-agent pipeline.

### `openrabbit describe`

Fetches one PR, loads indexed repository context when available, and prints a read-only summary, changed-file walkthrough, risk areas, and testing focus. It uses the same configured model provider as `openrabbit review`, but it never publishes comments or mutates the pull request.

```bash
openrabbit describe --pr 42 --repo owner/repo
openrabbit --quiet describe --pr 42 --repo owner/repo
```

### `openrabbit improve`

Fetches one PR, loads indexed repository context when available, and prints read-only improvement suggestions for changed lines. Suggestions are grounded to changed files and changed new-side lines before they are shown. The command uses the same configured model provider as `openrabbit review`, but it never applies patches, pushes commits, or posts comments.

```bash
openrabbit improve --pr 42 --repo owner/repo
openrabbit --quiet improve --pr 42 --repo owner/repo
```

### `openrabbit start`

Runs the polling service in the foreground, records PR polling state under `.openrabbit/state.json`, and reviews new PRs or new head commits automatically.

```bash
openrabbit start --workspace . --repo owner/repo
```

The first poll seeds state without reviewing every already-open PR. After that, new PRs and changed head SHAs trigger the same review-and-publish path as `openrabbit review`. Same-SHA updates, such as label or description changes, are logged and skipped.

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

Copy `.env.example` to `.env` if you want compose to pass a GitHub token or a custom workspace path into the containerized CLI. The `openrabbit` image packages the CLI, but local source install is still the most direct workflow for reviewing a working repository because `openrabbit init` needs write access to create `.openrabbit/`.

## GitHub Actions

Use [docs/github-actions.md](docs/github-actions.md) for a self-hosted or configured-runner workflow recipe. A copyable example lives at [examples/github-actions/openrabbit-review.yml](examples/github-actions/openrabbit-review.yml).

## Repository Layout

```text
src/
  cli/           Typer entry point and subcommands
  configs/       YAML and environment configuration
  github_/       GitHub REST client, PR parser, polling, publisher
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

## Contributing

Issues and PRs are welcome. Each planned piece of work is tracked as `OP-N` on the issue tracker. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.

## License

Apache 2.0.
