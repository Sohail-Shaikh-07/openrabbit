# Contributing to OpenRabbit

Thanks for thinking about contributing. This project is small enough that the workflow is informal, but a few conventions keep the history readable.

## Local setup

You will need Python 3.12+, Poetry, and Git. Docker is optional but useful for running Qdrant.

```bash
git clone https://github.com/Sohail-Shaikh-07/openrabbit.git
cd openrabbit
poetry install
pre-commit install
```

## Running the checks

The same gates that run in CI run locally:

```bash
poetry run ruff check .
poetry run black --check .
poetry run mypy
poetry run pytest
```

If any of these are red on your branch, fix them before opening a PR.

## Task IDs

Every piece of work is tracked under an ID like `OP-12`. The number is sequential and never gets reused, even when work is abandoned. Branches and commits carry the ID so it is easy to follow a change end to end.

- Branch name: `feature/op-12-short-name` or `fix/op-12-short-name`
- Commit subject: `feat(op-12): short verb-led summary`
- PR title: `[OP-12] Short description`
- PR body: closes the matching issue with `Closes #N`

## Pull requests

Open the PR against `main`. Keep changes scoped to one task ID. If you find an unrelated cleanup along the way, file a new issue for it instead of bundling it in.

Every PR needs:

- All CI checks green
- A short summary of what changed
- A short note on how it was tested

A maintainer self-reviews their own PRs before merging.

## Style notes

- Prefer editing existing files over creating new ones
- Public APIs are typed and `mypy --strict` is on
- Tests live under `tests/` mirroring the `src/` package structure
- No commented-out code, no TODO placeholders, no half-finished implementations
- README, commit messages, and PR descriptions read as plain prose, not as auto-generated changelogs

## Reporting bugs

Open an issue with what you ran, what you expected, and what happened. Include logs and Python and OS versions.

## License

By contributing you agree to license your contribution under Apache 2.0.
