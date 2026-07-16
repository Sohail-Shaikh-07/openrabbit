"""Backend contracts for OpenRabbit PR memory."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from inspect import Parameter, signature
from typing import Any, Protocol, cast, runtime_checkable

from agents.models import Finding
from memory.models import FindingComparison, PullRequestMemoryHistory, ReviewMemoryWrite


def repository_path_key(value: object) -> str:
    """Normalize separators without rewriting literal top-level directories."""
    if not isinstance(value, str):
        return ""
    return "/".join(part for part in value.strip().replace("\\", "/").split("/") if part)


def repository_paths_match(
    left: object,
    right: object,
    *,
    repository_paths: Iterable[object] = (),
) -> bool:
    """Compare repository paths, recognizing an explicit diff-side alias.

    A path is treated as a diff alias only when removing its ``a/`` or ``b/``
    prefix produces the other path. Known repository paths keep literal top-level
    directories named ``a`` or ``b`` from being rewritten.
    """
    left_key = repository_path_key(left)
    right_key = repository_path_key(right)
    if not left_key or not right_key:
        return left_key == right_key
    if left_key == right_key:
        return True

    known = {repository_path_key(path) for path in repository_paths}
    for candidate, other in ((left_key, right_key), (right_key, left_key)):
        if candidate in known:
            continue
        if candidate.startswith(("a/", "b/")) and candidate[2:] == other:
            return True
    return False


def paths_match_any(
    value: object,
    expected_paths: Iterable[object],
    *,
    repository_paths: Iterable[object] = (),
) -> bool:
    """Return whether *value* matches one expected repository path."""
    return any(
        repository_paths_match(value, expected, repository_paths=repository_paths)
        for expected in expected_paths
    )


def _accepts_keyword(method: object, keyword: str) -> bool:
    """Detect support for a keyword without invoking the backend method."""
    try:
        parameters = signature(cast(Callable[..., Any], method)).parameters
    except (TypeError, ValueError):
        return False
    return keyword in parameters or any(
        parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def compare_with_history_compat(
    backend: PullRequestMemoryBackend,
    *,
    repo: str,
    pr_number: int,
    head_sha: str,
    current_findings: Iterable[Finding],
    preserve_paths: Iterable[str],
) -> FindingComparison:
    """Call new or legacy memory backends without hiding backend errors."""
    if _accepts_keyword(backend.compare_with_history, "preserve_paths"):
        path_backend = cast(PathPreservingPullRequestMemoryBackend, backend)
        return path_backend.compare_with_history(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            current_findings=current_findings,
            preserve_paths=preserve_paths,
        )
    return backend.compare_with_history(
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        current_findings=current_findings,
    )


def record_review_compat(
    backend: PullRequestMemoryBackend,
    *,
    repo: str,
    pr_number: int,
    head_sha: str,
    findings: Iterable[Finding],
    context_loaded: bool,
    comments_posted: bool,
    preserve_paths: Iterable[str],
) -> ReviewMemoryWrite:
    """Persist through new or legacy memory backends without hiding errors."""
    if _accepts_keyword(backend.record_review, "preserve_paths"):
        path_backend = cast(PathPreservingPullRequestMemoryBackend, backend)
        return path_backend.record_review(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            findings=findings,
            context_loaded=context_loaded,
            comments_posted=comments_posted,
            preserve_paths=preserve_paths,
        )
    return backend.record_review(
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        findings=findings,
        context_loaded=context_loaded,
        comments_posted=comments_posted,
    )


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


class PathPreservingPullRequestMemoryBackend(Protocol):
    """Optional extension implemented by backends that preserve skipped paths."""

    def compare_with_history(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        current_findings: Iterable[Finding],
        preserve_paths: Iterable[str] = (),
    ) -> FindingComparison:
        """Compare findings while retaining records for skipped paths."""

    def record_review(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        findings: Iterable[Finding],
        context_loaded: bool,
        comments_posted: bool,
        preserve_paths: Iterable[str] = (),
    ) -> ReviewMemoryWrite:
        """Persist findings while retaining records for skipped paths."""
