"""Structured logging setup for the CLI.

We pin a single ``structlog`` configuration that the rest of the codebase
imports from ``cli.logging``. CLI flags decide the level; modules just call
``get_logger(__name__)``.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.stdlib import BoundLogger


def configure(level: int = logging.INFO) -> None:
    """Configure structlog + stdlib logging.

    Args:
        level: A standard ``logging`` level. ``WARNING`` for ``--quiet``,
            ``DEBUG`` for ``--verbose``, ``INFO`` by default.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> BoundLogger:
    """Return a bound structlog logger for the given module name."""
    logger: BoundLogger = structlog.get_logger(name)
    return logger
