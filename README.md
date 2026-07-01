# OpenRabbit

OpenRabbit is a self-hosted AI code reviewer for GitHub pull requests. You run it on your own machine, point it at a repo, and it leaves inline review comments on new PRs the way a teammate would.

Nothing leaves your machine. No SaaS account, no cloud inference, no telemetry. The source code stays on your laptop or your server.

## Why it exists

Most AI code reviewers ship as a SaaS. That means uploading proprietary code to a third party, paying per seat, and trusting another vendor with one of the most sensitive parts of your workflow. OpenRabbit is the opposite trade-off: bring your own machine, bring your own model, keep the code in your network.

## How a review happens

1. You open a pull request on GitHub.
2. OpenRabbit polls the repo every 60 seconds and notices the PR.
3. It pulls the diff, looks up relevant context from the repo (architecture notes, related code, your team's coding rules), and runs a set of specialized review agents in parallel.
4. The agents return findings. A ranker merges duplicates and drops low-signal noise.
5. OpenRabbit posts the surviving comments as a GitHub review.

The model behind the agents is a QLoRA-adapted Qwen2.5-Coder-7B trained specifically for code review, with Ollama as the default runtime.

## Status

v1.0.0 is complete. All six development phases have shipped.

| Phase | Focus | Status |
| ----- | ----- | ------ |
| 1 | CLI, configuration, Docker, testing | Done |
| 2 | GitHub integration and PR polling | Done |
| 3 | Repository-aware retrieval with Qdrant | Done |
| 4 | Multi-agent review system | Done |
| 5 | Fine-tuning the reviewer model | Done |
| 6 | Benchmarks and v1.0 release | Done |

## Requirements

- Python 3.12 or 3.13
- Poetry
- A GitHub personal access token with `repo` scope
- Ollama (for running the local model)
- Docker (optional, for running Qdrant alongside the daemon)

## Quick start

### With Docker (recommended)

```bash
cp .env.example .env
# edit .env and add your GITHUB_TOKEN
docker compose up -d
docker compose exec openrabbit openrabbit --help
```

This starts the OpenRabbit daemon and Qdrant together. Your repo is mounted read-only at `/workspace` inside the container.

### From source

```bash
poetry install
poetry run openrabbit --help
poetry run openrabbit init
```

`openrabbit init` writes a `.codereviewer/` folder into the current repository with template configuration files you can edit before starting the daemon.

## Configuration

After running `openrabbit init`, edit the files under `.codereviewer/`:

```
.codereviewer/
  config.yml          Main settings (model, polling interval, GitHub token)
  architecture.md     High-level description of the repo's architecture
  coding_rules.md     Style and design rules you want enforced in reviews
  security_rules.md   Security checks to apply in every review
  review_examples.md  Examples of good and bad review comments for your codebase
  ignore.txt          Glob patterns for files to skip (like lock files)
```

A minimal `config.yml`:

```yaml
github:
  token: ghp_...          # or use the GITHUB_TOKEN env var
repository:
  target: owner/repo      # GitHub repo to watch

model:
  provider: ollama
  name: qwen2.5-coder:7b

polling:
  interval_seconds: 60
```

Any value in `config.yml` can be overridden by an environment variable. For example, `GITHUB__TOKEN=...` overrides `github.token`.

## CLI commands

### `openrabbit start`

Runs the polling daemon in the foreground. Press Ctrl+C to stop.

```bash
poetry run openrabbit start --workspace . --repo owner/repo
```

### `openrabbit review`

Runs a one-off review of a specific PR, executes the configured local model agents, and prints ranked findings. Useful for testing your configuration or previewing what OpenRabbit would say.

```bash
poetry run openrabbit review --pr 42
poetry run openrabbit review --pr 42 --dry-run   # print only, do not post to GitHub
```

### `openrabbit index`

Scans the current repository and builds (or rebuilds) the RAG index in Qdrant. Run this once after `init` and again whenever the codebase changes significantly.

```bash
poetry run openrabbit index
```

### `openrabbit init`

Writes the `.codereviewer/` configuration scaffold into a target directory.

```bash
poetry run openrabbit init --path /path/to/repo
```

### `openrabbit install-model`

Downloads and installs the OpenRabbit-Reviewer-v1 LoRA adapter from Hugging Face Hub.

```bash
poetry run openrabbit install-model
poetry run openrabbit install-model --model-id myorg/my-adapter --token hf_...
```

## Platform support

OpenRabbit runs on Linux, macOS, and Windows. The CI matrix covers all three. If you find a platform-specific bug, open an issue with your OS and Python version.

## Repository layout

```
src/
  cli/           Typer entry point and subcommands
  configs/       YAML and env configuration
  github_/       GitHub REST client and PR parser
  rag/           Repository scanner, chunker, retriever (Qdrant)
  agents/        Multi-agent review pipeline (LangGraph)
  ranking/       Comment deduplication and ranking
  models/        Model serving (Ollama, vLLM, transformers)
  finetuning/    QLoRA training and evaluation pipeline
  benchmarks/    Evaluation harness for measuring review quality
  api/           Local FastAPI surface
tests/
scripts/
  train.py        Run a QLoRA fine-tuning job
  smoke_test.py   Cross-platform install verification
```

## Benchmarks

The `benchmarks` package provides an evaluation harness for measuring how well OpenRabbit finds known issues in a set of test diffs.

```python
from benchmarks import BenchmarkRunner, BenchmarkScorer, BenchmarkCase

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

## Fine-tuning and local models

OpenRabbit trains `OpenRabbit-Reviewer-v1` as a QLoRA adapter on top of `Qwen/Qwen2.5-Coder-7B-Instruct`. The runtime uses a local Ollama model name from `.codereviewer/config.yml`.

See [docs/model-finetuning.md](docs/model-finetuning.md) for the Google Colab free-tier training flow, adapter packaging, Ollama import, and local OpenRabbit configuration.

## Development

```bash
poetry install
poetry run pytest
poetry run ruff check .
poetry run black --check .
poetry run mypy
```

Pre-commit hooks ship with the repo. After `poetry install`, run `pre-commit install` once to wire them up.

To verify the install works on your platform:

```bash
poetry run python scripts/smoke_test.py
```

## Contributing

Issues and PRs are welcome. Each piece of work is tracked as `OP-N` on the issue tracker. If you want to pick something up, comment on the issue and grab it.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## License

Apache 2.0.
