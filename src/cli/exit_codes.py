"""Stable exit code surface for the CLI.

Keeping these as named constants prevents drift between commands and makes
shell-script callers easier to reason about.
"""

from __future__ import annotations

from typing import Final

OK: Final[int] = 0
USER_ERROR: Final[int] = 1
INTERNAL_ERROR: Final[int] = 2
NOT_IMPLEMENTED: Final[int] = 3
