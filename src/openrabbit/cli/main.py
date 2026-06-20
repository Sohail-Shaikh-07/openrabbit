"""OpenRabbit CLI entry point.

The Typer app is wired here; concrete command bodies live under
``openrabbit.cli.commands`` so they remain unit-testable without going through
the CLI runner.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console

from openrabbit import __version__
from openrabbit.cli import exit_codes
from openrabbit.cli import logging as orlog
from openrabbit.cli.commands.init import InitConflict, run_init

app = typer.Typer(
    name="openrabbit",
    help="Open-source, self-hosted AI Pull Request Review platform.",
    no_args_is_help=True,
    add_completion=False,
)

_console = Console()
_err = Console(stderr=True)


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
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only show warnings and errors."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug-level logs."),
) -> None:
    """OpenRabbit CLI root."""
    if quiet and verbose:
        _err.print("[red]--quiet and --verbose are mutually exclusive.[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR)
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    orlog.configure(level=level)


@app.command()
def init(
    path: Path = typer.Option(
        Path("."),
        "--path",
        "-p",
        exists=False,
        file_okay=False,
        dir_okay=True,
        help="Target repository directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing files in .codereviewer/.",
    ),
) -> None:
    """Create the ``.codereviewer/`` configuration scaffold."""
    target = path.resolve()
    try:
        result = run_init(target, force=force)
    except InitConflict as exc:
        _err.print(
            "[red]Refusing to overwrite existing files. "
            "Re-run with --force to replace them:[/red]"
        )
        for conflict in exc.conflicts:
            _err.print(f"  - {conflict}")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except FileNotFoundError:
        _err.print(f"[red]Target directory does not exist: {target}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except NotADirectoryError:
        _err.print(f"[red]Target path is not a directory: {target}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None

    _console.print(f"[green]Initialized OpenRabbit in {result.scaffold_dir}[/green]")
    for path_ in result.created:
        _console.print(f"  [green]+[/green] {path_.relative_to(target)}")
    for path_ in result.overwritten:
        _console.print(f"  [yellow]~[/yellow] {path_.relative_to(target)} (overwritten)")


def _not_implemented(command: str, phase: str) -> None:
    _err.print(f"[yellow]{command}: not implemented yet ({phase}).[/yellow]")
    raise typer.Exit(code=exit_codes.NOT_IMPLEMENTED)


@app.command()
def start() -> None:
    """Start the OpenRabbit daemon (polling + agents)."""
    _not_implemented("start", "Phase 2")


@app.command()
def stop() -> None:
    """Stop the running OpenRabbit daemon."""
    _not_implemented("stop", "Phase 2")


@app.command()
def index() -> None:
    """Scan the current repository and rebuild the RAG index."""
    _not_implemented("index", "Phase 3")


@app.command()
def review(
    pr: int = typer.Option(..., "--pr", help="Pull request number to review."),
) -> None:
    """Run a one-off review against a specific pull request."""
    _ = pr
    _not_implemented("review", "Phase 2 + 4")


if __name__ == "__main__":  # pragma: no cover
    app()
