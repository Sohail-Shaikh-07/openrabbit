# Optional Knowledge Connectors

OpenRabbit's default review loop remains local-first and service-free. Optional knowledge connectors are future adapters that can add context from MCP servers, web search, other repositories, Jira, Linear, or document systems after the user explicitly configures them.

The OP-95 scope is design and adapter boundaries only. No connector runs during review, describe, ask, improve, index, memory, or eval unless a later version adds explicit runtime support.

## Contract

Connector implementations conform to `KnowledgeConnector` from `knowledge.connectors`.

- `KnowledgeConnectorRequest` describes the repository, PR number, head SHA, changed paths, changed symbols, query, and item limit.
- `KnowledgeItem` is one sanitized, bounded, read-only context snippet.
- `KnowledgeConnectorHealth` reports availability through a health check without mutating state.
- `normalize_knowledge_items` bounds text, redacts common tokens, and sorts snippets deterministically.

Connectors return untrusted context. They do not change the required model output schema, bypass changed-line grounding, or publish GitHub comments directly. A connector can only provide source-labeled prompt guidance for the review pipeline to consider.

## Source Boundaries

### MCP

An MCP connector may query explicitly configured local or remote MCP servers for docs, tickets, architecture notes, or product context. It must use user-approved server configuration and must fail open when a server is unavailable.

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
- Connector failures produce warnings or unavailable health states and the review continues with diff, local memory, linked GitHub issues, and RAG context.
- Connector snippets are prompt guidance only and are always labeled by source.
- Connector data is treated as untrusted context and cannot override OpenRabbit's safety, grounding, or publishing rules.
- Optional connector configuration must name token environment variables rather than storing token values in repository config.

## Future Configuration Shape

```yaml
knowledge:
  connectors:
    mcp:
      enabled: false
    web_search:
      enabled: false
    multi_repo:
      enabled: false
      repositories: []
    jira:
      enabled: false
      token_env: JIRA_API_TOKEN
    linear:
      enabled: false
      token_env: LINEAR_API_KEY
```

This shape is intentionally documented before runtime support. Enabling a connector in future versions must require installed optional dependencies, explicit configuration, and a passing health check.

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
