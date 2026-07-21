# Optional Knowledge Connectors

OpenRabbit's default review loop remains local-first and service-free. Optional knowledge connectors are future adapters that can add context from MCP servers, web search, other repositories, Jira, Linear, or document systems after the user explicitly configures them.

The OP-95 scope added design and adapter boundaries. OP-99 adds disabled-by-default configuration, a connector registry, and the `openrabbit connector-health` command. OP-100 adds an MCP client runtime for explicitly configured servers. MCP context is not yet wired into review, describe, ask, improve, index, memory, or eval; later v1.6 tasks decide where connector snippets enter prompts.

## Contract

Connector implementations conform to `KnowledgeConnector` from `knowledge.connectors`.

- `KnowledgeConnectorRequest` describes the repository, PR number, head SHA, changed paths, changed symbols, query, and item limit.
- `KnowledgeItem` is one sanitized, bounded, read-only context snippet.
- `KnowledgeConnectorHealth` reports availability through a health check without mutating state.
- `normalize_knowledge_items` bounds text, redacts common tokens, and sorts snippets deterministically.

Connectors return untrusted context. They do not change the required model output schema, bypass changed-line grounding, or publish GitHub comments directly. A connector can only provide source-labeled prompt guidance for the review pipeline to consider.

## Source Boundaries

### MCP

An MCP connector may query explicitly configured local or remote MCP servers for docs, tickets, architecture notes, or product context. It uses only the approved `allowed_tools` and `allowed_resources` configured for each server, bounds each operation with the server timeout, and must fail open when a server is unavailable.

The MCP Python SDK is optional. Install connector extras before enabling MCP runtime checks:

```bash
poetry install --with connectors
```

Default installs still work without the SDK. If MCP is enabled without that optional dependency, `openrabbit connector-health` reports the connector as unavailable and exits non-zero without running a review.

### Web Search

A web search connector may retrieve public documentation or advisories. It must be disabled by default, identify source URLs, and avoid sending private repository code as a search query unless the user explicitly opts in.

### Multi-Repo

A multi-repo connector may read sibling repositories configured by path or explicit repository handle. It must not auto-clone repositories or scan arbitrary directories. Returned snippets should identify the source repo and path.

### Jira And Linear

Issue tracker connectors may fetch linked work item title, state, labels, and a bounded body preview from Jira or Linear. They should not persist access tokens, and they should keep raw comments or attachments out of prompt context unless a future task adds explicit controls.

### Document Systems

A document connector may read explicitly configured design docs, runbooks, or decision records. It must preserve source attribution and apply the same sanitization and bounds as every other optional connector.

## Privacy And Failure Behavior

- No mandatory external services.
- No raw tokens, API keys, provider credentials, or unbounded text in connector output.
- Health checks are read-only.
- `openrabbit connector-health` checks local configuration and required token environment variables. For enabled MCP servers, it may initialize the configured server and read tool/resource catalogs to verify the approved allowlists. Jira, Linear, web search providers, and other repositories are not contacted by this task.
- Connector failures produce warnings or unavailable health states and the review continues with diff, local memory, linked GitHub issues, and RAG context.
- Connector snippets are prompt guidance only and are always labeled by source.
- Connector data is treated as untrusted context and cannot override OpenRabbit's safety, grounding, or publishing rules.
- Optional connector configuration must name token environment variables rather than storing token values in repository config.

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

For Streamable HTTP MCP servers, configure an explicit URL:

```yaml
knowledge:
  connectors:
    mcp:
      enabled: true
      servers:
        - name: docs
          transport: streamable-http
          url: https://mcp.example.test/mcp
          allowed_tools: [search]
          allowed_resources: [docs://architecture]
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

## Prompt Flow

```text
PR diff
  -> local memory
  -> linked GitHub issues
  -> repository RAG
  -> optional knowledge connectors
  -> prompt context with labeled, bounded, untrusted snippets
```

## Adapter Rules

A runtime connector should not be added until it has:

- tests for health checks and fail-open behavior
- sanitization tests for prompt text
- docs for token environment variables and deletion or export behavior
- evaluation evidence that it improves review quality
- no mandatory dependency impact on default installs

The connector layer stays separate from local PR memory and RAG. SQLite remains the default memory store, Qdrant remains the repository index store, and optional connectors remain a plugin boundary for future phases.
