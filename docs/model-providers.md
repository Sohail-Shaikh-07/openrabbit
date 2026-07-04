# Model Provider Setup

OpenRabbit can review pull requests with a local Ollama model, the official OpenAI API, or any endpoint that follows the OpenAI chat completions shape. Ollama remains the default because OpenRabbit is local-first.

## Provider Matrix

| Provider | Config value | Requires API key | Requires `base_url` | Best for |
| --- | --- | --- | --- | --- |
| Ollama | `ollama` | No | No | Fully local review with `qwen2.5-coder:7b` or a local OpenRabbit model |
| OpenAI | `openai` | Yes | No | Hosted OpenAI models through `https://api.openai.com/v1` |
| OpenAI-compatible | `openai-compatible` | Yes | Yes | vLLM, LiteLLM, OpenRouter-style gateways, local OpenAI-compatible servers, or enterprise gateways |

`vllm` and `transformers` may appear in the schema as future provider names, but they are not wired into the review-agent factory yet. Use `ollama`, `openai`, or `openai-compatible` for current reviews.

## Secret Rules

Never put API key values in `.openrabbit/config.yml`.

OpenRabbit rejects inline model secrets such as:

```yaml
model:
  api_key: sk_secret_value
```

Use `model.api_key_env` instead. OpenRabbit reads the variable named there and sends the secret only in the provider request header.

The GitHub token follows separate settings under `github.token_env`. It can also come from `OPENRABBIT_GITHUB__TOKEN`.

## Ollama

Install and start Ollama, then pull a local model:

```bash
ollama pull qwen2.5-coder:7b
ollama run qwen2.5-coder:7b
```

If `ollama serve` says port `11434` is already in use, Ollama is already running. You can continue.

`.openrabbit/config.yml`:

```yaml
model:
  provider: ollama
  model_name: qwen2.5-coder:7b
  base_model: qwen2.5-coder:7b
```

Verify locally:

```bash
ollama list
openrabbit review --pr 42 --repo owner/repo --dry-run
```

Use this path when you want source code and prompts to stay on your own machine.

## Official OpenAI API

Set the API key in your shell.

PowerShell:

```powershell
setx OPENAI_API_KEY "sk_your_key_here"
```

Open a new terminal after `setx`, or load it into the current PowerShell session:

```powershell
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
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

Do not set `base_url` for the official OpenAI provider. OpenRabbit rejects `model.base_url` unless `provider` is `openai-compatible`.

Verify:

```bash
openrabbit review --pr 42 --repo owner/repo --dry-run
```

## OpenAI-Compatible Endpoints

Use this provider for a server or gateway that exposes:

```text
/v1/chat/completions
```

Examples include vLLM OpenAI server, LiteLLM, OpenRouter-style endpoints, local gateways, and enterprise gateways.

PowerShell:

```powershell
setx OPENAI_COMPATIBLE_API_KEY "your_gateway_key_here"
$env:OPENAI_COMPATIBLE_API_KEY = [Environment]::GetEnvironmentVariable("OPENAI_COMPATIBLE_API_KEY", "User")
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

Set `base_url` to the endpoint root, not the full chat completions URL. These are valid examples:

```text
http://localhost:8000/v1
https://gateway.example.com/v1
```

These are not valid:

```text
localhost:8000/v1
http://localhost:8000/v1/chat/completions
```

For local servers that do not enforce authentication, set the configured environment variable to a harmless placeholder such as `local-key`. OpenRabbit still sends it only in the request header.

## GitHub Token

Model provider keys are separate from GitHub auth. For GitHub reviews, set a token with repository access:

PowerShell:

```powershell
setx GITHUB_TOKEN "github_pat_your_token_here"
$env:GITHUB_TOKEN = [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")
```

macOS/Linux:

```bash
export GITHUB_TOKEN="github_pat_your_token_here"
```

OpenRabbit reads GitHub tokens in this order:

1. `OPENRABBIT_GITHUB__TOKEN`
2. The variable named by `github.token_env`, default `GITHUB_TOKEN`
3. On Windows, persistent User or Machine environment variables

## Environment Overrides

Any config value can be overridden with `OPENRABBIT_` environment variables. Use double underscores for nested fields:

```bash
OPENRABBIT_MODEL__PROVIDER=openai
OPENRABBIT_MODEL__MODEL_NAME=gpt-4.1-mini
OPENRABBIT_MODEL__API_KEY_ENV=OPENAI_API_KEY
```

Do not use `OPENRABBIT_MODEL__API_KEY`. It is rejected for the same reason as inline `model.api_key`: secrets should be stored in dedicated environment variables and referenced by name.

## Troubleshooting

`no GitHub token found`

Set `GITHUB_TOKEN`, `OPENRABBIT_GITHUB__TOKEN`, or the variable named by `github.token_env`. On Windows, open a new terminal after `setx` or load the persistent value into the current session.

`Model provider 'openai' requires an API key`

Set the environment variable named by `model.api_key_env`, usually `OPENAI_API_KEY`.

`model.base_url is required`

`provider: openai-compatible` requires an HTTP or HTTPS `base_url`.

`model.base_url is only supported`

Remove `base_url` unless `provider` is `openai-compatible`.

`unsupported provider`

Use `ollama`, `openai`, or `openai-compatible`. Other provider names are not implemented in the review-agent factory yet.
