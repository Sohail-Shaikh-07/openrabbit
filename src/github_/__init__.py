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
from github_.polling import EventKind, Handler, PollEvent, PollingService
from github_.pr import ParsedFile, PullRequestParser, PullRequestPayload
from github_.repository import RepositoryHandle
from github_.state import (
    FileStateStore,
    InMemoryStateStore,
    PollState,
    SeenPullRequest,
    StateStore,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "Branch",
    "BranchCommit",
    "CommitAuthor",
    "CommitInfo",
    "DiffLine",
    "EventKind",
    "FileStateStore",
    "GitHubAPIError",
    "GitHubAuthError",
    "GitHubClient",
    "Handler",
    "Hunk",
    "InMemoryStateStore",
    "Label",
    "LineKind",
    "ParsedFile",
    "PollEvent",
    "PollState",
    "PollingService",
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
    "SeenPullRequest",
    "StateStore",
    "User",
    "parse_patch",
]
