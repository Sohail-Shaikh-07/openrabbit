# OpenRabbit

OpenRabbit is a local-first AI pull request reviewer for GitHub repositories. You run it on your own machine, point it at a repo, and use a local model to inspect pull request diffs with repository context.

The core trade-off is privacy and ownership: source code is reviewed on your laptop or server, with Ollama as the default model runtime and Qdrant as the repository context store.

## Current Status

OpenRabbit is at `v1.0.0`. The foundation is in place: CLI, configuration, GitHub PR parsing, repository indexing, local multi-agent review, ranking, fine-tuning utilities, and release validation.

The current manual review flow is:

1. OpenRabbit fetches a pull request from GitHub.
2. It parses commits, changed files, hunks, and changed-line evidence.
3. Enabled review agents run against the diff with strict JSON output prompts.
4. Findings are grounded to changed files and changed lines.
5. A ranker removes duplicates, orders the findings, and drops ungrounded output.
6. The CLI prints the summary locally.
7. If `--dry-run` is not set and findings exist, OpenRabbit posts them as a GitHub review.

Automatic polling-to-review execution is still an important next gap. Today, `openrabbit review --dry-run` is the safe preview path, while `openrabbit review` publishes grounded findings when there is something useful to post. See [docs/pr-agent-gap-analysis.md](docs/pr-agent-gap-analysis.md) for the current comparison against PR-Agent and the recommended roadmap.

## What Works Today

| Area | Current capability |
| --- | --- |
| CLI | `init`, `index`, `review`, `start`, `install-model`, `--quiet`, `--verbose`, `--version` |
| Configuration | `.openrabbit/config.yml`, `OPENRABBIT_...` environment overrides, Windows persistent env fallback for GitHub tokens |
| GitHub | PAT auth, repository handles, PR metadata, commits, changed files, hunks, binary-file handling |
| Local model | Ollama-backed review agents using `model.model_name` from config |
| Agents | Security, performance, architecture, bug, and test coverage agents |
| Prompting | Changed-line evidence first, strict JSON contract, no speculative findings |
| Ranking | Severity/confidence scoring, duplicate removal, changed-line grounding |
| RAG | Repository scanner, chunker, embeddings, Qdrant vector store, indexing CLI |
| Fine-tuning | QLoRA training, dataset cleaning/formatting, evaluation, adapter packaging |
| Benchmarks | Benchmark runner, scorer, and profiler for measuring review quality |

## Requirements

- Python 3.12 or 3.13
- Poetry, or `pip` for a local package install
- GitHub personal access token with access to the target repository
- Ollama for local review inference
- Qdrant for repository indexing
- Docker, optional, for running Qdrant locally

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

### `openrabbit review`

Fetches one PR, runs the enabled local agents, grounds findings to the diff, prints a ranked summary, and publishes a GitHub review when findings exist.

```bash
openrabbit review --pr 42 --repo owner/repo --dry-run
openrabbit review --pr 42 --repo owner/repo
openrabbit --quiet review --pr 42 --repo owner/repo --dry-run
openrabbit --verbose review --pr 42 --repo owner/repo --dry-run
```

Use `--dry-run` to print the result locally without posting comments. Empty findings are not posted, so clean PRs do not receive noisy review comments.

### `openrabbit start`

Runs the polling service in the foreground and records PR polling state under `.openrabbit/state.json`.

```bash
openrabbit start --workspace . --repo owner/repo
```

In the current release, `start` detects and logs polling events. Wiring those events into the full review-and-publish pipeline is a tracked next gap.

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
from benchmarks import BenchmarkCase, BenchmarkRunner, BenchmarkScorer

cases = [
    BenchmarkCase(
        case_id="example-001",
        diff="...",
        known_issues=["SQL injection in login handler"],
    )
]

runner = BenchmarkRunner(agents=[...])
report = await runner.run(cases)

scorer = BenchmarkScorer()
scored = scorer.score(report, cases)
print(f"macro F1: {scored.macro_f1:.3f}")
```

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
