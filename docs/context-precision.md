# Context Precision And Troubleshooting

OpenRabbit context precision is the set of rules that decides which local and optional external evidence reaches model-facing commands. It applies to `review`, `describe`, `ask`, `improve`, and eval reporting.

The goal is practical: keep changed-line and compressed diff evidence first, add only relevant repository and connector context, show why sources were selected or dropped, and keep every source bounded.

## What Gets Packed

OpenRabbit packs prompt context in this order:

1. Changed-line evidence from the PR diff
2. Compressed diff evidence
3. Repository RAG snippets from Qdrant
4. Optional connector snippets
5. Local PR memory
6. Linked GitHub issue summaries
7. Local quality gate diagnostics

Each source has its own default token budget. These source budgets are intentionally separate:

| Source | Default budget |
| --- | ---: |
| `changed_line_evidence` | 3000 |
| `diff` | 6000 |
| `rag` | 4500 |
| `connector` | 1200 |
| `memory` | 1600 |
| `linked_issue` | 1200 |
| `quality` | 2000 |

These are deterministic estimates, not provider billing tokens. They are used to prevent one noisy source from crowding out the rest of the review context.

## Retrieval Reasons

Repository RAG prefers direct PR signals before broad semantic matches.

Common selected reasons:

| Reason | Meaning |
| --- | --- |
| `changed_file` | Context came from a file changed by the PR. |
| `changed_symbol` | Context matched a changed function, method, or class name. |
| `related_test` | Context came from a related test path or test naming pattern. |
| `nearby_path` | Context came from a nearby directory or module. |
| `scoped_guideline` | A guideline file applied to the changed path scope. |
| `architecture_doc` | A known architecture or design document was relevant. |
| `semantic` | The vector search matched the PR query without a stronger direct reason. |
| `linked_issue` | A linked issue key or issue summary matched the context source. |

Direct reasons are usually stronger than `semantic`. A healthy run for a focused PR should show some mix of changed file, changed symbol, nearby path, related test, scoped guideline, or architecture document evidence.

## Connector Relevance

Optional connectors are disabled by default. When enabled, their snippets are treated as untrusted evidence and scored before prompt packing.

Connector relevance can increase when a snippet matches:

- a linked issue key such as `SEC-42` or `ENG-42`
- changed file paths
- changed symbols
- repository handles
- source kind fit, such as Jira or Linear issue context for linked work items
- provider score
- text overlap with the PR title, body, branch, commits, paths, or ask question

Weak connector snippets are dropped with `weak_connector_relevance`. Relevant snippets beyond the connector item limit are dropped with `connector_item_limit`.

Selected connector hits can include `relevance_score`, `relevance_reasons`, and `provider_score` metadata in diagnostics. Use these fields to confirm that linked issue context or multi-repo snippets are being selected for concrete PR reasons, not because the connector returned broadly similar text.

## Large Low-Risk Files

Large generated docs, lock-style files, manifests, or other low-risk oversized changes can be summarized before agent review. The prompt clearly marks these summaries with text that says the summary is not a full diff.

Diagnostics expose:

- `large_low_risk_files`
- `large_low_risk_changes`
- `large_low_risk_diff_lines`

This keeps risky code diffs visible while still telling the model and the operator that a large low-risk change exists.

## Diagnostics Fields

Model-facing JSON output includes `context_diagnostics`. Eval reports aggregate the same local fields into `totals`, `context_sources`, `dashboard.cards`, and `dashboard.charts`.

Useful fields:

| Field | Use |
| --- | --- |
| `candidate_items` | How many context items were considered. |
| `selected_items` | How many context items were selected. |
| `dropped_items` | How many context items were dropped. |
| `selected_sources` | Which source labels reached context. |
| `selected_reasons` | Why selected items were retrieved. |
| `scores` | Count, min, max, and average relevance score. |
| `rag.dropped_reasons` | Why repository RAG items were dropped. |
| `connectors.dropped_reasons` | Why connector items were dropped. |
| `source_budgets` | Default budget by source. |
| `source_packing` | Estimated token use and over-budget state by source. |
| `prompt_packing.estimated_tokens` | Estimated selected context size for RAG hits. |

Eval reports add dashboard-friendly rollups for selected sources, retrieval reasons, RAG contribution, connector contribution, connector relevance scores, source budget usage, source budget overages, prompt tokens, and large low-risk summaries.

## Troubleshooting Missing Context

Start with local state:

```bash
openrabbit index --health
openrabbit review --pr 42 --repo owner/repo --dry-run
```

Check the review summary for `Context: loaded` and `Context sources:`. If the run is `diff only`, confirm Qdrant is running and the repository has been indexed:

```bash
docker compose up -d qdrant
openrabbit index --workspace .
```

If RAG is loaded but expected files are missing:

- Confirm the file is not hidden, generated, dependency, binary, or oversized according to scanner rules.
- Re-run `openrabbit index --workspace .` after large source or documentation changes.
- Check whether the PR changed paths or symbols that should point to the missing file.
- Look for `selected_reasons`; only `semantic` results may mean the PR lacks direct path, symbol, test, or guideline signals.
- Check `rag.dropped_reasons` for `top_k_limit` or index availability reasons.

If linked GitHub issue context is missing:

- Confirm the PR body, title, branch, commits, or comments include a linked issue reference GitHub can expose.
- Check `linked_issue_count` in command output or eval reports.
- Confirm the GitHub token can read the linked issue if it belongs to a private repository.

## Troubleshooting Noisy Context

If too much irrelevant context reaches the model:

- Review `selected_sources` and `selected_reasons` in JSON output or eval reports.
- Prefer scoped guideline files near the affected paths instead of broad repository-wide guidance.
- Re-index after removing stale docs or generated files from the repository index.
- Check whether a connector is returning broad snippets that only match weak text overlap.
- Use connector configuration to lower `max_items` for a noisy source.
- Keep web search private-code queries disabled unless the selected MCP search provider is approved for repository metadata.

Noisy connector context usually shows weak source labels, low connector relevance scores, or repeated `weak_connector_relevance` drops. Noisy RAG context usually shows mostly `semantic` reasons without changed file, symbol, nearby path, related test, guideline, or architecture matches.

## Troubleshooting Budget Pressure

Budget pressure appears when `source_packing` marks a source as over budget or eval reports show `source_budget_overages`.

Common fixes:

- Keep large generated or lock-style changes out of the main review path when they are not the risk being reviewed.
- Split very large PRs when risky code changes are mixed with generated artifacts.
- Lower connector `max_items` when external snippets are repeatedly over budget.
- Add focused repository guidelines near the changed paths so broad docs are less likely to dominate retrieval.
- Re-run eval with the same `--scenario-group` values and compare `context_prompt_tokens_by_pr`.

Budget overage does not mean a review failed. It means OpenRabbit had more candidate text than the source budget allowed and had to pack a bounded subset.

## Eval Interpretation

Use `openrabbit eval` to compare context behavior across repeatable PR sets:

```bash
openrabbit eval --repo owner/repo --prs 1,2,3 --scenario-group context=1,2,3
```

Inspect these report sections first:

- `totals.context_selected_sources`
- `totals.context_selected_reasons`
- `totals.rag_selected_items`
- `totals.connector_selected_items`
- `totals.source_budget_estimated_tokens`
- `totals.source_budget_overages`
- `dashboard.charts.context_selected_sources`
- `dashboard.charts.context_selected_reasons`
- `dashboard.charts.context_prompt_tokens_by_pr`
- `dashboard.charts.large_low_risk_by_pr`

For regression work, the packaged v1.7 context precision corpus covers changed-symbol context, linked connector evidence, and large low-risk summaries:

```python
from benchmarks import DEFAULT_V1_7_CONTEXT_PRECISION_CORPUS, load_benchmark_cases

cases = load_benchmark_cases(DEFAULT_V1_7_CONTEXT_PRECISION_CORPUS)
```

## Privacy Boundaries

Context diagnostics expose counts, source labels, selected reasons, dropped reasons, score summaries, and estimated prompt-packing size. They do not include raw prompt text, raw tool output, API keys, credentials, or unbounded connector bodies.

Connector snippets remain optional, bounded, redacted, source-labeled, and fail open. A connector failure should reduce available context, not block local review.
