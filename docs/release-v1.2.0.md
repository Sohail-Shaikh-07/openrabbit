# OpenRabbit v1.2.0 Release Notes

OpenRabbit v1.2.0 turns the v1.1 review loop into a more stateful, controllable, and measurable reviewer. It preserves the local-first default while adding PR memory, re-review controls, command handling, stronger RAG visibility, and a repeatable PR quality log.

## Highlights

- Added local PR conversation memory and finding fingerprints.
- Added incremental and full review modes.
- Added publish mode for `openrabbit improve`.
- Added PR comment commands in polling mode.
- Added `openrabbit index --health` and review context provenance.
- Added review profiles, path filters, path-specific instructions, large PR limits, generated-file defaults, and skipped-path reporting.
- Added `openrabbit eval` for JSON and Markdown PR regression logs.

## Upgrade Notes

- Package version is `1.2.0`.
- Python support remains `>=3.12,<3.14`.
- The default provider remains Ollama.
- Existing `.openrabbit/config.yml` files continue to work.
- New review controls are optional and default to `profile: assertive`, no path include/exclude filters, `max_files: 80`, `max_changed_lines: 4000`, and `include_generated: false`.
- Local memory remains enabled by default and stores state under `.openrabbit/state/openrabbit.db` unless configured otherwise.

## Validation

The release branch should pass:

- `python -m pytest`
- `python -m ruff check $(git ls-files '*.py')`
- `python -m black --check .`
- `python -m mypy`
- `python scripts/smoke_test.py`

The release workflow also checks that a `v1.2.0` tag matches the package version before publishing artifacts.
