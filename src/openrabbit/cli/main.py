"""OpenRabbit CLI entry point.

This module wires the Typer application. The subcommands implemented in OP-1 are
placeholders that print their intent and exit. They are filled in across later
tasks:

- ``init``    -> OP-2 / Phase 1
- ``start``   -> Phase 2 (polling) + Phase 4 (agents)
- ``stop``    -> Phase 2
- ``index``   -> Phase 3 (RAG indexing)
- ``review``  -> Phase 2 (manual trigger) + Phase 4
"""

from __future__ import annotations

import typer
from rich.console import Console

from openrabbit import __version__

app = typer.Typer(
    name="openrabbit",
    help="Open-source, self-hosted AI Pull Request Review platform.",
    no_args_is_help=True,
    add_completion=False,
)

_console = Console()


def _version_callback(value: bool) -> None:
    if value:
        _console.print(f"openrabbit {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """OpenRabbit CLI root."""


@app.command()
def init() -> None:
    """Create the ``.codereviewer/`` configuration scaffold in the current repo.

    Full implementation lands in OP-2.
    """
    _console.print("[yellow]init: not implemented yet (OP-2).[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def start() -> None:
    """Start the OpenRabbit daemon (polling + agents)."""
    _console.print("[yellow]start: not implemented yet (Phase 2).[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def stop() -> None:
    """Stop the running OpenRabbit daemon."""
    _console.print("[yellow]stop: not implemented yet (Phase 2).[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def index() -> None:
    """Scan the current repository and rebuild the RAG index."""
    _console.print("[yellow]index: not implemented yet (Phase 3).[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def review(
    pr: int = typer.Option(..., "--pr", help="Pull request number to review."),
) -> None:
    """Run a one-off review against a specific pull request."""
    _ = pr
    _console.print("[yellow]review: not implemented yet (Phase 2 + 4).[/yellow]")
    raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
