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

This is early. Phase 1 (the foundation) is landing now. Each phase ships behind real commits and PRs on this repo.

| Phase | Focus | Days |
| ----- | ----- | ---- |
| 1 | CLI, configuration, Docker, testing | 1 to 5 |
| 2 | GitHub integration and PR polling | 6 to 10 |
| 3 | Repository-aware retrieval with Qdrant | 11 to 16 |
| 4 | Multi-agent review system | 17 to 23 |
| 5 | Fine-tuning the reviewer model | 24 to 30 |
| 6 | Benchmarks and v1.0 release | 31 to 40 |

## Quick start

You will need a GitHub personal access token with `repo` scope. From there pick one of two ways to run.

### With Docker (recommended)

```bash
cp .env.example .env
# put your GITHUB_TOKEN inside .env, then:
docker compose up -d
docker compose exec openrabbit openrabbit --help
```

This brings up the OpenRabbit daemon and Qdrant together. Your repo is mounted read-only at `/workspace` inside the container.

### From source

You will need Python 3.12+ and Poetry.

```bash
poetry install
poetry run openrabbit --help
poetry run openrabbit init
```

`openrabbit init` drops a `.codereviewer/` folder into the current repository with template configuration files you can edit:

```
.codereviewer/
  config.yml
  architecture.md
  coding_rules.md
  security_rules.md
  review_examples.md
  ignore.txt
```

Fill those in to describe your repo. Everything OpenRabbit reviews from then on uses that context.

## Repository layout

```
openrabbit/
  src/
    cli/           Typer entry point and subcommands
    configs/       YAML and env configuration
    github_/       GitHub REST client and PR parser (Phase 2)
    rag/           Repository scanner, chunker, retriever (Phase 3)
    agents/        Multi-agent review pipeline (Phase 4)
    ranking/       Comment deduplication and ranking
    models/        Model serving (Ollama, vLLM, transformers)
    finetuning/    QLoRA training pipeline (Phase 5)
    api/           Local FastAPI surface
  tests/
```

## Development

```bash
poetry install
poetry run pytest
poetry run ruff check .
poetry run black --check .
poetry run mypy
```

Pre-commit hooks ship with the repo. After `poetry install`, run `pre-commit install` once to wire them up.

## Contributing

Issues and PRs are welcome. Each piece of work is tracked as `OP-N` on the issue tracker. If you want to pick something up, comment on the issue and grab it.

## License

Apache 2.0.
