"""Command-line interface for OpenRabbit.

Entry point is exported as the ``openrabbit`` console script via Poetry.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    """Load the Typer application only when callers request it."""
    if name == "app":
        from cli.main import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["app"]
