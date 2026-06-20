"""GitHub client, PR parser, and polling service (Phase 2)."""

from __future__ import annotations

from github_.client import (
    DEFAULT_BASE_URL,
    GitHubAPIError,
    GitHubAuthError,
    GitHubClient,
)
from github_.diff import DiffLine, Hunk, LineKind, parse_patch
from github_.models import (
    Branch,
    BranchCommit,
    CommitAuthor,
    CommitInfo,
    Label,
    PullRequest,
    PullRequestCommit,
    PullRequestFile,
    PullRequestRef,
    PullRequestState,
    PullRequestSummary,
    Repository,
    Review,
    ReviewComment,
    ReviewEvent,
    User,
)
from github_.pr import ParsedFile, PullRequestParser, PullRequestPayload
from github_.repository import RepositoryHandle

__all__ = [
    "DEFAULT_BASE_URL",
    "Branch",
    "BranchCommit",
    "CommitAuthor",
    "CommitInfo",
    "DiffLine",
    "GitHubAPIError",
    "GitHubAuthError",
    "GitHubClient",
    "Hunk",
    "Label",
    "LineKind",
    "ParsedFile",
    "PullRequest",
    "PullRequestCommit",
    "PullRequestFile",
    "PullRequestParser",
    "PullRequestPayload",
    "PullRequestRef",
    "PullRequestState",
    "PullRequestSummary",
    "Repository",
    "RepositoryHandle",
    "Review",
    "ReviewComment",
    "ReviewEvent",
    "User",
    "parse_patch",
]
