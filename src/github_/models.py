"""Typed models for the subset of the GitHub REST API OpenRabbit uses.

The shapes here are intentionally narrower than what the API returns. We only
parse the fields downstream code reads. Extra fields are ignored so a GitHub
schema addition does not break the client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PullRequestState = Literal["open", "closed", "all"]
ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
FileStatus = Literal["added", "modified", "removed", "renamed", "copied", "changed", "unchanged"]


class _APIObject(BaseModel):
    """Base model that ignores extra fields and forbids assignment of unknown ones."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class User(_APIObject):
    """A GitHub user, narrowed to login and id."""

    login: str
    id: int


class Repository(_APIObject):
    """A GitHub repository, narrowed to identifying fields."""

    id: int
    name: str
    full_name: str
    default_branch: str
    private: bool


class RepositoryFileContent(_APIObject):
    """Repository content metadata and encoded bytes returned by GitHub."""

    type: str
    encoding: str | None = None
    content: str | None = None
    size: int


class BranchCommit(_APIObject):
    """The commit at the tip of a branch."""

    sha: str


class Branch(_APIObject):
    """A repository branch."""

    name: str
    commit: BranchCommit
    protected: bool = False


class PullRequestRef(_APIObject):
    """The ``head`` or ``base`` ref on a pull request."""

    ref: str
    sha: str
    label: str


class Label(_APIObject):
    name: str


class Issue(_APIObject):
    """GitHub issue metadata used as compact PR context."""

    number: int
    title: str
    state: Literal["open", "closed"]
    body: str | None = None
    labels: list[Label] = Field(default_factory=list)
    html_url: str


class PullRequestSummary(_APIObject):
    """Trimmed pull request representation used by polling and listing."""

    number: int
    title: str
    state: Literal["open", "closed"]
    draft: bool = False
    user: User
    head: PullRequestRef
    base: PullRequestRef
    created_at: datetime
    updated_at: datetime
    labels: list[Label] = Field(default_factory=list)


class PullRequest(PullRequestSummary):
    """Full pull request representation with body and merge info."""

    body: str | None = None
    merged: bool = False
    mergeable: bool | None = None
    mergeable_state: str | None = None


class PullRequestFile(_APIObject):
    """A file changed in a pull request."""

    sha: str | None = None
    filename: str
    status: FileStatus
    additions: int
    deletions: int
    changes: int
    patch: str | None = None
    previous_filename: str | None = None


class CommitAuthor(_APIObject):
    """Authoring metadata on a commit."""

    name: str | None = None
    email: str | None = None
    date: datetime | None = None


class CommitInfo(_APIObject):
    """The nested ``commit`` object on a pull request commit."""

    message: str
    author: CommitAuthor | None = None
    committer: CommitAuthor | None = None


class PullRequestCommit(_APIObject):
    """A commit on a pull request."""

    sha: str
    commit: CommitInfo


class ReviewComment(_APIObject):
    """One inline comment posted as part of a review."""

    path: str
    body: str
    line: int | None = None
    side: Literal["LEFT", "RIGHT"] | None = None
    start_line: int | None = None
    start_side: Literal["LEFT", "RIGHT"] | None = None


class Review(_APIObject):
    """Result of posting a review."""

    id: int
    body: str | None = None
    state: str
    html_url: str
    submitted_at: datetime | None = None


class PullRequestReview(_APIObject):
    """A review already present on a pull request."""

    id: int
    user: User
    body: str | None = None
    state: str
    commit_id: str | None = None
    html_url: str
    submitted_at: datetime | None = None


class PullRequestReviewComment(_APIObject):
    """An inline review comment already present on a pull request."""

    id: int
    user: User
    body: str
    path: str
    line: int | None = None
    commit_id: str | None = None
    html_url: str
    created_at: datetime
    updated_at: datetime


class IssueComment(_APIObject):
    """A top-level issue/PR conversation comment."""

    id: int
    user: User
    body: str
    html_url: str
    created_at: datetime
    updated_at: datetime
