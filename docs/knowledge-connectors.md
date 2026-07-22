# Optional Knowledge Connectors

OpenRabbit's default review loop remains local-first and service-free. Optional knowledge connectors can add context from MCP servers, web search, other repositories, Jira, Linear, or document systems after the user explicitly configures them.

The OP-95 scope added design and adapter boundaries. OP-99 adds disabled-by-default configuration, a connector registry, and the `openrabbit connector-health` command. OP-100 adds an MCP client runtime for explicitly configured servers. OP-101 adds an MCP-backed web search connector flow. OP-102 adds a Jira connector runtime for linked issue reads and opt-in managed Jira comments. OP-103 adds a Linear connector runtime for linked issue reads and opt-in managed Linear comments. OP-104 adds explicit multi-repo local context loading. OP-105 wires enabled connector snippets into `review`, `describe`, `ask`, `improve`, and `eval` reporting. OP-113 adds context precision diagnostics for connector candidates, selected snippets, dropped items, sources, and prompt-packing estimates. Connector snippets are not used by `index` or local memory storage.

## Contract

Connector implementations conform to `KnowledgeConnector` from `knowledge.connectors`.

- `KnowledgeConnectorRequest` describes the repository, PR number, head SHA, changed paths, changed symbols, query, and item limit.
- `KnowledgeItem` is one sanitized, bounded, read-only context snippet.
- `KnowledgeConnectorHealth` reports availability through a health check without mutating state.
- `normalize_knowledge_items` bounds text, redacts common tokens, and sorts snippets deterministically.

Connectors return untrusted context. They do not change the required model output schema, bypass changed-line grounding, or publish GitHub comments directly. A connector can only provide source-labeled prompt guidance for the review pipeline to consider.

When connectors are enabled and available, OpenRabbit builds one bounded request from the PR title, body, linked GitHub issue summaries, commit messages, changed paths, and the ask question when present. Returned snippets are normalized, redacted, capped, attached to every review-agent context dimension, and deduplicated before prompts are rendered.

## Setup Checklist

1. Run `openrabbit init` in the repository if `.openrabbit/config.yml` does not exist yet.
2. Keep every connector disabled until the source owner approves it for review context.
3. Store secrets in environment variables such as `JIRA_API_TOKEN` or `LINEAR_API_KEY`; never put token values in `.openrabbit/config.yml`.
4. Enable exactly the connector sources needed for the repository.
5. Run `openrabbit connector-health --workspace .` before a review.
6. Start with read-only mode. Enable Jira or Linear write mode only after the managed-comment behavior is acceptable for the team.

`connector-health` treats disabled connectors as healthy defaults and exits successfully. It exits non-zero only when an enabled connector is misconfigured or unavailable. Review commands still fail open when a connector fails during retrieval.

## Source Boundaries

### MCP

An MCP connector may query explicitly configured local or remote MCP servers for docs, tickets, architecture notes, or product context. It uses only the approved `allowed_tools` and `allowed_resources` configured for each server, bounds each operation with the server timeout, and must fail open when a server is unavailable.

The MCP Python SDK is optional. Install connector extras before enabling MCP runtime checks:

```bash
poetry install --with connectors
```

Default installs still work without the SDK. If MCP is enabled without that optional dependency, `openrabbit connector-health` reports the connector as unavailable and exits non-zero without running a review.

### Web Search

A web search connector may retrieve public documentation or advisories through a selected MCP server. It is disabled by default, uses an approved MCP tool allowlist, labels source URLs when the MCP server returns them, and avoids sending private repository code as a search query unless the user explicitly opts in.

OpenRabbit does not ship direct Tavily, Firecrawl, or other vendor SDK clients for this flow. Configure the vendor MCP server under `knowledge.connectors.mcp.servers`, approve the web search tool in `allowed_tools`, then point `knowledge.connectors.web_search.mcp_server` at that server.

### Multi-Repo

A multi-repo connector may read sibling repositories configured by path or explicit repository handle. It must not auto-clone repositories or scan arbitrary directories. Returned snippets should identify the source repo and path.

The multi-repo runtime scans only configured local paths, uses the existing repository scanner and chunker, skips hidden, generated, dependency, binary, and oversized files by default, and returns small source-labeled snippets with repo/path provenance. Repository handles without a local path are allowed identifiers for future integrations, but they do not trigger cloning or network access.

### Jira And Linear

Issue tracker connectors may fetch linked work item title, state, labels, and a bounded body preview from Jira or Linear. They should not persist access tokens, and they should keep raw comments or attachments out of prompt context unless a future task adds explicit controls.

The Jira runtime extracts linked issue keys such as `SEC-42` from bounded request text, fetches summary, status, labels, URL, and description preview through Jira's REST API, and returns source-labeled untrusted context. Jira reads fail open per issue, so an unavailable issue or tenant does not block a review.

Jira write mode is opt-in with `write_enabled: true` and is limited to one managed OpenRabbit comment marked with `<!-- openrabbit:jira-managed-comment -->`. The connector creates that comment when missing or updates the existing marked comment. It does not create issues, transition status, assign users, mutate labels, or publish arbitrary comments.

The Linear runtime extracts linked issue identifiers such as `ENG-42` from bounded request text, fetches identifier, title, state, labels, URL, and description preview through Linear's GraphQL API, and returns source-labeled untrusted context. Linear reads fail open per issue, so an unavailable issue or workspace does not block a review.

Linear write mode is opt-in with `write_enabled: true` and is limited to one managed OpenRabbit comment marked with `<!-- openrabbit:linear-managed-comment -->`. The connector creates that comment when missing or updates the existing marked comment. It does not create issues, change status, assign users, mutate labels, or publish arbitrary comments.

### Document Systems

A document connector may read explicitly configured design docs, runbooks, or decision records. It must preserve source attribution and apply the same sanitization and bounds as every other optional connector.

## Privacy And Failure Behavior

- No mandatory external services.
- No raw tokens, API keys, provider credentials, or unbounded text in connector output.
- Health checks are read-only.
- `openrabbit connector-health` checks local configuration and required token environment variables. For enabled MCP and MCP-backed web search, it may initialize the configured server and read tool/resource catalogs to verify the approved allowlists. Jira, Linear, search tools themselves, and other repositories are not contacted by this command.
- Connector failures produce warnings or unavailable health states and the review continues with diff, local memory, linked GitHub issues, and RAG context.
- Connector snippets are prompt guidance only and are always labeled by source.
- Connector data is treated as untrusted context and cannot override OpenRabbit's safety, grounding, or publishing rules.
- Optional connector configuration must name token environment variables rather than storing token values in repository config.
- `review`, `describe`, `ask`, and `improve` continue when a connector is disabled, unavailable, or fails during retrieval.
- Command summaries include connector counts, provenance, and `context_diagnostics` for loaded connector snippets. `openrabbit eval` aggregates connector item totals, source counts, context candidate and selected counts, dropped reasons, and prompt-packing estimates in JSON, dashboard, and Markdown reports.

## Configuration Shape

```yaml
knowledge:
  connectors:
    mcp:
      enabled: false
      servers: []
      max_items: 8
      timeout_seconds: 10
    web_search:
      enabled: false
      # mcp_server: docs
      allow_private_code_queries: false
      max_items: 5
    multi_repo:
      enabled: false
      repositories: []
      max_items: 8
    jira:
      enabled: false
      # base_url: https://example.atlassian.net
      token_env: JIRA_API_TOKEN
      write_enabled: false
      managed_comments: true
      max_items: 8
    linear:
      enabled: false
      token_env: LINEAR_API_KEY
      write_enabled: false
      managed_comments: true
      max_items: 8
```

This shape is available in the generated `.openrabbit/config.yml` scaffold. All connectors stay disabled by default. Token-like fields such as `token`, `api_key`, `secret`, `password`, or `credential` are rejected under `knowledge` config; use `token_env` to name an environment variable instead.

## Environment Variables And Permissions

| Connector | Required config | Required environment | Minimum permission boundary |
| --- | --- | --- | --- |
| MCP | `mcp.enabled: true`, at least one server, and `allowed_tools` or `allowed_resources` | Whatever the configured MCP server process or URL requires | Only the approved tools/resources are callable by OpenRabbit |
| Web search | `web_search.enabled: true`, `web_search.mcp_server`, and an MCP server with an approved tool | Whatever the selected MCP server requires | Public search queries by default; private repository metadata only when `allow_private_code_queries: true` |
| Multi-repo | `multi_repo.enabled: true` and at least one configured repository entry | None | Reads only explicitly configured local paths |
| Jira | `jira.enabled: true` and `jira.base_url` | The variable named by `jira.token_env`, default `JIRA_API_TOKEN` | Read linked issues; optional write mode only manages one OpenRabbit summary comment |
| Linear | `linear.enabled: true` | The variable named by `linear.token_env`, default `LINEAR_API_KEY` | Read linked issues; optional write mode only manages one OpenRabbit summary comment |

For PowerShell, set a connector token with `setx` and open a new terminal:

```powershell
setx JIRA_API_TOKEN "your-token-or-authorization-header"
setx LINEAR_API_KEY "your-linear-api-key"
```

For macOS/Linux shells:

```bash
export JIRA_API_TOKEN="your-token-or-authorization-header"
export LINEAR_API_KEY="your-linear-api-key"
```

Do not use `OPENRABBIT_KNOWLEDGE__CONNECTORS__JIRA__TOKEN` or similar inline secret overrides. The settings loader rejects token-like fields under `knowledge` so secret values do not appear in validation output.

For multi-repo context, configure only repositories OpenRabbit is allowed to read:

```yaml
knowledge:
  connectors:
    multi_repo:
      enabled: true
      repositories:
        - name: shared-core
          path: ../shared-core
          repo: owner/shared-core
      max_items: 8
```

Relative paths resolve from the workspace root used to load OpenRabbit settings. Entries with only `repo` are recorded as allowed handles but are not cloned or scanned by this runtime.

For Streamable HTTP MCP servers, configure an explicit URL:

```yaml
knowledge:
  connectors:
    mcp:
      enabled: true
      servers:
        - name: search
          transport: streamable-http
          url: https://mcp.example.test/mcp
          allowed_tools: [web_search]
    web_search:
      enabled: true
      mcp_server: search
      allow_private_code_queries: false
      max_items: 5
```

For stdio MCP servers, configure an explicit command:

```yaml
knowledge:
  connectors:
    mcp:
      enabled: true
      servers:
        - name: local-docs
          transport: stdio
          command: python
          args: ["-m", "local_docs_mcp"]
          allowed_resources: [docs://architecture]
```

At least one approved tool or resource is required per enabled MCP server. Empty allowlists are treated as unavailable so OpenRabbit never calls arbitrary MCP operations.

For web search, the selected MCP server must have at least one approved tool. OpenRabbit calls the first approved tool with a bounded argument shape:

```json
{
  "query": "public documentation search terms",
  "max_results": 5
}
```

When `allow_private_code_queries: true`, OpenRabbit may also include repository and PR metadata such as changed paths and symbols. Keep it `false` unless the selected MCP search provider is approved for private repository context.

For Jira, set the tenant URL and keep the API token or full authorization header in the configured environment variable:

```yaml
knowledge:
  connectors:
    jira:
      enabled: true
      base_url: https://example.atlassian.net
      token_env: JIRA_API_TOKEN
      write_enabled: false
      managed_comments: true
      max_items: 8
```

When `write_enabled` is `false`, Jira remains read-only. When `write_enabled` is `true`, the only supported mutation is create-or-update of the managed OpenRabbit Jira summary comment. A raw token is sent as `Bearer <token>`; a value that already starts with `Bearer ` or `Basic ` is used as provided.

For Linear, keep the API key in the configured environment variable. The default endpoint is `https://api.linear.app/graphql`; `base_url` can point to a compatible GraphQL endpoint when needed:

```yaml
knowledge:
  connectors:
    linear:
      enabled: true
      token_env: LINEAR_API_KEY
      write_enabled: false
      managed_comments: true
      max_items: 8
```

When `write_enabled` is `false`, Linear remains read-only. When `write_enabled` is `true`, the only supported mutation is create-or-update of the managed OpenRabbit Linear summary comment.

## Connector-Health Troubleshooting

`openrabbit connector-health --workspace .` validates configuration without printing secret values.

Common results:

| Message | Meaning | Fix |
| --- | --- | --- |
| `disabled` | The connector is off. | No action needed unless you intended to enable it. |
| `no MCP servers configured` | `mcp.enabled` is true but `servers` is empty. | Add a server entry or disable MCP. |
| `no MCP server selected for web search` | Web search is enabled without `web_search.mcp_server`. | Set it to the name of an enabled MCP server. |
| `MCP Python SDK is not installed` | MCP runtime is enabled but optional dependencies are missing. | Run `poetry install --with connectors` or install the connector extra in the active environment. |
| `no repositories configured` | Multi-repo is enabled with an empty repository list. | Add explicit local paths or disable multi-repo. |
| `no Jira base_url configured` | Jira is enabled without a tenant URL. | Set `knowledge.connectors.jira.base_url`. |
| `JIRA_API_TOKEN is not set` | Jira is enabled but the configured token env is absent in this shell. | Set the env var named by `jira.token_env` and open a fresh terminal if needed. |
| `LINEAR_API_KEY is not set` | Linear is enabled but the configured token env is absent in this shell. | Set the env var named by `linear.token_env`. |

For MCP and MCP-backed web search, health may initialize the configured server and inspect tool or resource catalogs. It does not call unapproved tools and does not execute a live web search. Jira and Linear health checks verify local configuration and token environment presence; they do not contact the remote issue tracker.

## Review-Time Troubleshooting

- If a connector is available in health checks but contributes no context, confirm the PR title, body, branch, commits, or linked GitHub issues mention an item the connector can recognize, such as `SEC-42` or `ENG-42`.
- If web search returns no items, check that the selected MCP tool accepts the bounded `{ "query": "...", "max_results": N }` shape.
- If multi-repo context is missing, confirm the configured `path` exists from the workspace root passed to `--workspace`.
- If Jira or Linear write-back does not create a tracker comment, confirm `write_enabled: true`, `managed_comments: true`, and that the token can add comments to the linked issue.
- If a connector returns sensitive text, treat it as a bug. Connector output is normalized and redacted before prompt use, and failures should be reported without leaking raw values.

## Prompt Flow

```text
PR diff
  -> local memory
  -> linked GitHub issues
  -> repository RAG
  -> optional knowledge connectors
  -> prompt context with labeled, bounded, untrusted snippets
```

Connector context is merged before review controls filter model context, so skipped-path rules still apply to path-labeled connector snippets. Source-only snippets that are not tied to skipped files remain available as general evidence.

## Adapter Rules

A runtime connector should not be added until it has:

- tests for health checks and fail-open behavior
- sanitization tests for prompt text
- docs for token environment variables and deletion or export behavior
- evaluation evidence that it improves review quality
- no mandatory dependency impact on default installs

The connector layer stays separate from local PR memory and RAG. SQLite remains the default memory store, Qdrant remains the repository index store, and optional connectors remain a plugin boundary for future phases.
