"""OpenRabbit CLI entry point.

The Typer app is wired here. Concrete command bodies live under
``cli.commands`` so they remain unit-testable without going through the
CLI runner.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import typer
from rich.console import Console

from cli import exit_codes
from cli import logging as orlog
from cli.commands.ask import render_answer, run_ask_blocking
from cli.commands.describe import render_description, run_describe_blocking
from cli.commands.eval import (
    parse_pr_numbers,
    render_eval_summary,
    run_eval_blocking,
)
from cli.commands.improve import render_improvements, run_improve_blocking
from cli.commands.index import run_index_blocking, run_qdrant_health_check_blocking
from cli.commands.init import InitConflict, run_init
from cli.commands.install_model import InstallResult, run_install_model
from cli.commands.memory import render_memory_summary, run_memory_inspect
from cli.commands.review import ReviewMode, render_summary, run_review_blocking
from cli.commands.start import StartError, run_start_blocking
from configs import ConfigNotFoundError, load_settings
from github_ import GitHubAPIError, GitHubAuthError

try:
    __version__ = version("openrabbit")
except PackageNotFoundError:  # pragma: no cover - editable install fallback
    __version__ = "0.0.0+local"

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
        help="Overwrite existing files in .openrabbit/.",
    ),
) -> None:
    """Create the ``.openrabbit/`` configuration scaffold."""
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


def _load_settings_or_exit(workspace: Path) -> object:
    """Load settings rooted at ``workspace`` or exit with a clear message."""
    try:
        return load_settings(workspace)
    except ConfigNotFoundError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None


@app.command()
def start(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository to watch, in owner/repo form. Overrides repository.target.",
    ),
) -> None:
    """Run the polling service in the foreground."""
    workspace = workspace.resolve()
    settings = _load_settings_or_exit(workspace)
    try:
        run_start_blocking(settings, workspace=workspace, repo=repo)  # type: ignore[arg-type]
    except StartError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except GitHubAuthError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None


@app.command()
def stop() -> None:
    """Stop the running OpenRabbit daemon."""
    _not_implemented("stop", "Phase 2")


@app.command()
def index(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    qdrant_host: str = typer.Option(
        "localhost",
        "--qdrant-host",
        help="Qdrant server host.",
    ),
    qdrant_port: int = typer.Option(
        6333,
        "--qdrant-port",
        help="Qdrant server port.",
    ),
    health: bool = typer.Option(
        False,
        "--health",
        help="Check Qdrant connectivity and list collections without indexing.",
    ),
) -> None:
    """Scan the current repository and rebuild the RAG index."""
    workspace = workspace.resolve()
    if health:
        health_result = run_qdrant_health_check_blocking(
            qdrant_host=qdrant_host,
            qdrant_port=qdrant_port,
        )
        if health_result.ok:
            collections = (
                ", ".join(health_result.collections) if health_result.collections else "none"
            )
            _console.print(f"[green]{health_result.message}[/green]")
            _console.print(f"  Collections: {collections}")
            return
        _err.print(f"[red]{health_result.message}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR)
    try:
        index_result = run_index_blocking(
            workspace, qdrant_host=qdrant_host, qdrant_port=qdrant_port
        )
    except Exception as exc:
        _err.print(f"[red]Indexing failed: {exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    _console.print(
        f"[green]Indexed {index_result.chunks_indexed} chunks from "
        f"{index_result.files_scanned} files.[/green]"
    )


@app.command()
def review(
    pr: int = typer.Option(..., "--pr", help="Pull request number to review."),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository to review, in owner/repo form. Overrides repository.target.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the review summary without posting comments to GitHub.",
    ),
    mode: ReviewMode = typer.Option(
        ReviewMode.INCREMENTAL,
        "--mode",
        case_sensitive=False,
        help="Review publish mode: incremental posts only new findings; full reposts all findings.",
    ),
) -> None:
    """Run a one-off review of a pull request and publish findings unless dry-run."""
    workspace = workspace.resolve()
    settings = _load_settings_or_exit(workspace)
    try:
        summary = run_review_blocking(
            settings,  # type: ignore[arg-type]
            number=pr,
            repo=repo,
            dry_run=dry_run,
            mode=mode,
        )
    except ValueError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except StartError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except (GitHubAuthError, GitHubAPIError) as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    import sys

    render_summary(summary, sys.stdout)


@app.command()
def describe(
    pr: int = typer.Option(..., "--pr", help="Pull request number to describe."),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository to describe, in owner/repo form. Overrides repository.target.",
    ),
) -> None:
    """Generate a read-only summary and walkthrough of a pull request."""
    workspace = workspace.resolve()
    settings = _load_settings_or_exit(workspace)
    try:
        summary = run_describe_blocking(
            settings,  # type: ignore[arg-type]
            number=pr,
            repo=repo,
        )
    except StartError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except (GitHubAuthError, GitHubAPIError) as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    import sys

    render_description(summary, sys.stdout)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask about the pull request."),
    pr: int = typer.Option(..., "--pr", help="Pull request number to ask about."),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository to ask about, in owner/repo form. Overrides repository.target.",
    ),
) -> None:
    """Ask an evidence-based question about a pull request."""
    workspace = workspace.resolve()
    settings = _load_settings_or_exit(workspace)
    try:
        summary = run_ask_blocking(
            settings,  # type: ignore[arg-type]
            number=pr,
            question=question,
            repo=repo,
        )
    except ValueError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except StartError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except (GitHubAuthError, GitHubAPIError) as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    import sys

    render_answer(summary, sys.stdout)


@app.command()
def improve(
    pr: int = typer.Option(..., "--pr", help="Pull request number to improve."),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository to improve, in owner/repo form. Overrides repository.target.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print improvement suggestions without posting them to GitHub.",
    ),
    publish: bool = typer.Option(
        False,
        "--publish",
        help="Publish grounded, actionable improvement suggestions to GitHub.",
    ),
) -> None:
    """Generate improvement suggestions for changed pull request lines."""
    if dry_run and publish:
        _err.print("[red]--dry-run and --publish are mutually exclusive.[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR)
    workspace = workspace.resolve()
    settings = _load_settings_or_exit(workspace)
    try:
        summary = run_improve_blocking(
            settings,  # type: ignore[arg-type]
            number=pr,
            repo=repo,
            dry_run=dry_run,
            publish=publish,
        )
    except ValueError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except StartError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except (GitHubAuthError, GitHubAPIError) as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    import sys

    render_improvements(summary, sys.stdout)


@app.command("eval")
def eval_command(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository to evaluate, in owner/repo form. Overrides repository.target.",
    ),
    prs: str = typer.Option(
        "1,2,3,4,5",
        "--prs",
        help="Comma or space separated PR numbers to evaluate.",
    ),
    output: Path = typer.Option(
        Path(".openrabbit/reports/review-eval.json"),
        "--output",
        "-o",
        help="JSON report path.",
    ),
    markdown: Path | None = typer.Option(
        Path(".openrabbit/reports/review-eval.md"),
        "--markdown",
        help="Markdown dashboard path. Use an explicit path to override the default.",
    ),
) -> None:
    """Run a local evaluation over selected pull requests and write a test log."""
    workspace = workspace.resolve()
    settings = _load_settings_or_exit(workspace)
    try:
        pr_numbers = parse_pr_numbers(prs)
        report = run_eval_blocking(
            settings,  # type: ignore[arg-type]
            repo=repo,
            prs=pr_numbers,
            output=output,
            markdown=markdown,
        )
    except ValueError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except (GitHubAuthError, GitHubAPIError) as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    import sys

    render_eval_summary(report, sys.stdout)


@app.command("memory")
def memory(
    pr: int = typer.Option(..., "--pr", help="Pull request number to inspect."),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        "-w",
        help="Path to the repo that contains .openrabbit/.",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        "-r",
        help="Repository to inspect, in owner/repo form. Overrides repository.target.",
    ),
) -> None:
    """Inspect local OpenRabbit memory for a pull request."""
    workspace = workspace.resolve()
    settings = _load_settings_or_exit(workspace)
    try:
        summary = run_memory_inspect(
            settings,  # type: ignore[arg-type]
            repo=repo,
            pr_number=pr,
        )
    except ValueError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    except StartError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None
    import sys

    render_memory_summary(summary, sys.stdout)


@app.command("install-model")
def install_model(
    model_id: str = typer.Option(
        "openrabbit/openrabbit-reviewer-v1",
        "--model-id",
        "-m",
        help="HuggingFace Hub repo ID to install.",
    ),
    install_dir: Path | None = typer.Option(
        None,
        "--install-dir",
        help="Directory to install the adapter into. Defaults to ~/.openrabbit/models/.",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        "-t",
        help="HuggingFace Hub token for private repos.",
        envvar="HF_TOKEN",
    ),
) -> None:
    """Download and install the OpenRabbit-Reviewer-v1 LoRA adapter."""
    try:
        result: InstallResult = run_install_model(
            model_id=model_id,
            install_dir=install_dir,
            token=token,
        )
    except (FileNotFoundError, RuntimeError, ImportError) as exc:
        _err.print(f"[red]install-model failed: {exc}[/red]")
        raise typer.Exit(code=exit_codes.USER_ERROR) from None

    _console.print(f"[green]Installed {result.model_id}[/green]")
    _console.print(f"  Adapter path: {result.install_dir}")


if __name__ == "__main__":  # pragma: no cover
    app()
