"""Command-line interface for OpenRabbit.

Entry point is exported as the ``openrabbit`` console script via Poetry.
"""

from __future__ import annotations

from openrabbit.cli.main import app

__all__ = ["app"]
