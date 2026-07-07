"""Backend contracts for OpenRabbit PR memory."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from agents.models import Finding
from memory.models import FindingComparison, PullRequestMemoryHistory, ReviewMemoryWrite


@runtime_checkable
class PullRequestMemoryBackend(Protocol):
    """Storage boundary for local and future plugin memory backends.

    Implementations must keep secrets out of persisted state. The default
    backend is SQLite, but graph and vector adapters can implement this same
    contract later without changing review orchestration.
    """

    def load_history(self, repo: str, pr_number: int) -> PullRequestMemoryHistory:
        """Return stored memory for one repository pull request."""

    def compare_with_history(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        current_findings: Iterable[Finding],
    ) -> FindingComparison:
        """Compare current findings with previously stored memory."""

    def record_review(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        findings: Iterable[Finding],
        context_loaded: bool,
        comments_posted: bool,
    ) -> ReviewMemoryWrite:
        """Persist one review run and its finding comparison."""
