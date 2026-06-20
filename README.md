# OpenRabbit

Open-source, self-hosted AI Pull Request Review platform.

OpenRabbit reads your repository, runs a multi-agent review pipeline over each pull request, and posts inline review comments back to GitHub. It is built to run entirely on your own machine — no SaaS, no cloud account, no code transmission.

## Status

Early development. Tracking the 40-day plan in [`.agent/roadmap.md`](.agent/roadmap.md).

| Phase | Scope | Days |
| --- | --- | --- |
| 1 | Foundation (CLI, config, Docker, tests) | 1–5 |
| 2 | GitHub integration (polling + PR parsing) | 6–10 |
| 3 | Repository-aware RAG (Qdrant) | 11–16 |
| 4 | Multi-agent review system (LangGraph) | 17–23 |
| 5 | Fine-tuning pipeline (Qwen2.5-Coder QLoRA) | 24–30 |
| 6 | Evaluation + release | 31–40 |

## Architecture

```
GitHub → Polling → Review Orchestrator → Multi-Agent System
                                              ↓
                                        Comment Ranker
                                              ↓
                                       Review Publisher → GitHub
```

Component design lives in [`.agent/architecture.md`](.agent/architecture.md). Agent contracts are in [`.agent/agent-specefication.md`](.agent/agent-specefication.md). RAG design is in [`.agent/rag-design.md`](.agent/rag-design.md). The fine-tuning plan is in [`.agent/fine-tunning.md`](.agent/fine-tunning.md).

## Requirements

- Python 3.12+
- Poetry
- Docker + Docker Compose (for Qdrant)
- A GitHub Personal Access Token with `repo` scope

## Local development

```bash
poetry install
poetry run openrabbit --help
poetry run pytest
```

## Contributing

Issues and pull requests are welcome. See open work at <https://github.com/Sohail-Shaikh-07/openrabbit/issues>.

## License

Apache 2.0.
