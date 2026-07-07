# OpenRabbit v1.1.0 Release Notes

OpenRabbit v1.1.0 hardens the self-hosted review loop and adds the first set of PR exploration tools inspired by PR-Agent while keeping OpenRabbit local-first by default.

## Highlights

- Added official OpenAI and OpenAI-compatible provider support alongside Ollama.
- Added read-only `openrabbit describe`, `openrabbit ask`, and `openrabbit improve` commands.
- Added changed-line grounding and token-aware diff compression to reduce noisy or ungrounded output.
- Added layered config: built-in defaults, optional `~/.openrabbit/config.yml`, repo config, and environment overrides.
- Added a GitHub Action recipe for configured or self-hosted runner use.
- Added a packaged v1.1 benchmark corpus for regression and provider comparison checks.

## Upgrade Notes

- Package version is `1.1.0`.
- Python support remains `>=3.12,<3.14`.
- The default provider remains Ollama.
- API keys should stay in environment variables. Do not put token values in `.openrabbit/config.yml` or `~/.openrabbit/config.yml`.
- User-level config is optional. Repository config continues to override user defaults.

## Validation

The release branch should pass:

- `poetry run pytest`
- `poetry run ruff check src tests scripts`
- `poetry run black --check src tests scripts`
- `poetry run mypy`
- `poetry run python scripts/smoke_test.py`
- `poetry build`

The release workflow also checks that a `v1.1.0` tag matches the package version before publishing artifacts.
