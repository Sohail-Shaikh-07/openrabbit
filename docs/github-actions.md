# GitHub Actions Recipe

OpenRabbit can run from GitHub Actions when the runner can reach the model provider you configure. This is a recipe for teams that want automation without a hosted OpenRabbit service.

## Recommended Runner Shapes

| Runner | Model provider | Good fit | Notes |
| --- | --- | --- | --- |
| Self-hosted runner on your machine or server | `ollama` | Local-first review with private code and local model inference | Ollama must be running on the runner, usually at `http://localhost:11434` |
| Self-hosted runner with Docker | `ollama` plus Qdrant | Local model plus repository context indexing | Keep Qdrant and Ollama state on the runner for faster repeated reviews |
| GitHub-hosted or self-hosted runner with outbound network | `openai` or `openai-compatible` | API-provider reviews without local GPU setup | Source snippets are sent to the configured provider, so use this only when acceptable for the repository |

Do not use `pull_request_target` for untrusted pull requests. It exposes more repository privileges than this recipe needs. Start with `pull_request` and `workflow_dispatch`.

## Required Permissions

For publishing review comments, the workflow needs:

```yaml
permissions:
  contents: read
  pull-requests: write
```

OpenRabbit can use `${{ github.token }}` through `OPENRABBIT_GITHUB__TOKEN`. For stricter org policies, create a fine-scoped token secret and pass it as `OPENRABBIT_GITHUB__TOKEN` instead.

## Self-Hosted Ollama Workflow

This is the local-first path. Copy [examples/github-actions/openrabbit-review.yml](../examples/github-actions/openrabbit-review.yml) into the target repository as:

```text
.github/workflows/openrabbit-review.yml
```

Runner requirements:

- Python 3.12 or 3.13
- `pipx` or a working Python `pip`
- Ollama installed and reachable from the runner
- The model already pulled, or enough time/disk to pull it during the workflow
- Optional: Docker and Qdrant if you want repository context indexing
- Runner labels matching the example: `self-hosted`, `linux`, and `openrabbit`

The workflow writes a minimal `.openrabbit/config.yml` if the repository has not committed one. For production use, commit your own `.openrabbit/` directory so review rules, architecture notes, and provider settings live beside the code.

The example installs OpenRabbit from the `OPENRABBIT_REF` environment value, which defaults to `main`. For production use, pin this to a release tag, for example:

```yaml
env:
  OPENRABBIT_REF: "v1.4.0"
```

Manual `workflow_dispatch` runs default to dry-run mode so you can inspect findings in the logs before posting comments. Set the `dry_run` input to `false` when you intentionally want a manual run to publish review comments. Pull request events publish by default because they use the normal review workflow.

## API Provider Workflow

For OpenAI:

```yaml
env:
  OPENRABBIT_GITHUB__TOKEN: ${{ github.token }}
  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Use this config:

```yaml
model:
  provider: openai
  model_name: gpt-4.1-mini
  api_key_env: OPENAI_API_KEY
```

For an OpenAI-compatible gateway:

```yaml
env:
  OPENRABBIT_GITHUB__TOKEN: ${{ github.token }}
  OPENAI_COMPATIBLE_API_KEY: ${{ secrets.OPENAI_COMPATIBLE_API_KEY }}
```

Use this config:

```yaml
model:
  provider: openai-compatible
  model_name: openai/gpt-oss-20b
  base_url: https://gateway.example.com/v1
  api_key_env: OPENAI_COMPATIBLE_API_KEY
```

Set `base_url` to the endpoint root, not `/chat/completions`.

## Dry Run First

Before posting comments, validate the workflow with:

```bash
openrabbit review --pr "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --dry-run
```

Then remove `--dry-run` after you are comfortable with the output.

The default read-only commands are also useful in Actions logs:

```bash
openrabbit describe --pr "$PR_NUMBER" --repo "$GITHUB_REPOSITORY"
openrabbit improve --pr "$PR_NUMBER" --repo "$GITHUB_REPOSITORY"
```

To publish improvement suggestions, use the explicit publish flag:

```bash
openrabbit improve --pr "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --publish
```

`improve --publish` only posts grounded suggestions. Suggestions with safe replacement snippets are posted as inline GitHub suggestion blocks; broader actionable suggestions are grouped in the review body.

## Repository Context

If Qdrant is available on the runner, index before review:

```bash
openrabbit index --workspace . --health --qdrant-host localhost --qdrant-port 6333
openrabbit index --workspace . --qdrant-host localhost --qdrant-port 6333
openrabbit review --pr "$PR_NUMBER" --repo "$GITHUB_REPOSITORY"
```

If Qdrant is unavailable, reviews still run in diff-only mode.

The example workflow runs `openrabbit index --health` before indexing and marks both index steps `continue-on-error: true`. That keeps PR review available even when Qdrant is not running, while still making the missing context visible in Actions logs.

When context is loaded, the review summary includes `Context sources:` with the indexed files that contributed retrieved context. This is useful in CI logs because it confirms whether the run used repository rules, docs, tests, or source-symbol chunks.

OpenRabbit checks for an existing RAG index before loading the embedding model during review, so runners without Qdrant do not need to download embedding weights just to complete a diff-only review.

## Troubleshooting

`no GitHub token found`

Pass `OPENRABBIT_GITHUB__TOKEN: ${{ github.token }}` or set `GITHUB_TOKEN` in the workflow environment.

`Model provider 'openai' requires an API key`

Set the provider key as a repository or organization secret and make sure `model.api_key_env` names that variable.

`Connection refused` for Ollama

The runner cannot reach Ollama. Start `ollama serve`, check port `11434`, or switch to an API provider.

The workflow is stuck pulling the model

Pull the model once on the self-hosted runner before enabling the workflow:

```bash
ollama pull qwen2.5-coder:7b
```

The workflow still runs `ollama pull` to make the dependency explicit, but a pre-pulled model should make the step fast.

No comments appear on forked PRs

GitHub may restrict token permissions for untrusted fork workflows. Keep the workflow on `pull_request`, review the logs, and only move to more privileged patterns after a security review.
