# Benchmark Corpus

OpenRabbit includes a small packaged regression corpus for measuring review quality during v1.1 development.

The corpus lives at `src/benchmarks/corpora/v1_1_regression.jsonl` and is loaded with `load_benchmark_cases()`. It is synthetic by design, so it can be committed, shared, and run in CI without exposing private repository code.

## What It Covers

The v1.1 corpus focuses on gaps that matter for a PR reviewer:

- SQL injection in changed application code
- Missing admin authorization on a sensitive route
- N+1 query behavior introduced inside a loop
- Optional value dereference without a guard
- New public helper with weak test coverage
- A risky hardcoded secret hidden inside a larger settings diff

## How To Run It

```python
from benchmarks import BenchmarkRunner, BenchmarkScorer, load_benchmark_cases

cases = load_benchmark_cases()
runner = BenchmarkRunner(agents=[...])
report = await runner.run(cases)

scored = BenchmarkScorer().score(report, cases)
print(f"macro F1: {scored.macro_f1:.3f}")
```

The runner accepts injected agents, so tests can use deterministic fake agents and local evaluations can use the production OpenRabbit agents with an Ollama or API-backed provider.

## Format

Each JSONL record must contain:

- `case_id`: unique non-empty string
- `title`: optional string
- `diff`: non-empty unified diff string
- `known_issues`: non-empty list of strings used by the scorer

Blank lines are ignored. Duplicate IDs, missing fields, invalid JSON, empty diffs, and invalid `known_issues` values fail fast with `CorpusFormatError`.

## Limits

This corpus is not a substitute for real-world evaluation. It is a regression baseline that catches obvious reviewer regressions and makes provider comparisons repeatable. The current scorer uses substring matching against known issue text, so use macro precision, recall, and F1 as directional signals rather than absolute product quality scores.
