"""High-level handle for a single GitHub repository.

The :class:`RepositoryHandle` bundles ``owner`` and ``repo`` with a
:class:`GitHubClient` so callers do not have to thread those names through
every API method. Polling, PR parsing, and the review publisher all build on
this handle.
"""

from __future__ import annotations

from dataclasses import dataclass

from github_.client import GitHubClient
from github_.models import (
    Branch,
    Issue,
    IssueComment,
    PullRequest,
    PullRequestCommit,
    PullRequestFile,
    PullRequestReview,
    PullRequestReviewComment,
    PullRequestState,
    PullRequestSummary,
    Repository,
    Review,
    ReviewComment,
    ReviewEvent,
)


@dataclass(frozen=True)
class RepositoryHandle:
    """A typed handle to one repository plus the client used to reach it."""

    owner: str
    repo: str
    client: GitHubClient

    @classmethod
    def from_full_name(cls, full_name: str, client: GitHubClient) -> RepositoryHandle:
        """Parse ``owner/repo`` and build a handle.

        Raises:
            ValueError: If ``full_name`` is not in ``owner/repo`` form.
        """
        if full_name.count("/") != 1 or not all(part.strip() for part in full_name.split("/")):
            raise ValueError(f"expected 'owner/repo', got {full_name!r}")
        owner, repo = full_name.split("/")
        return cls(owner=owner, repo=repo, client=client)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    async def get(self) -> Repository:
        return await self.client.get_repository(self.owner, self.repo)

    async def get_file_text(self, path: str, ref: str, *, max_bytes: int) -> str:
        return await self.client.get_file_text(
            self.owner,
            self.repo,
            path,
            ref,
            max_bytes=max_bytes,
        )

    async def list_branches(self) -> list[Branch]:
        return await self.client.list_branches(self.owner, self.repo)

    async def list_pull_requests(
        self, *, state: PullRequestState = "open"
    ) -> list[PullRequestSummary]:
        return await self.client.list_pull_requests(self.owner, self.repo, state=state)

    async def get_pull_request(self, number: int) -> PullRequest:
        return await self.client.get_pull_request(self.owner, self.repo, number)

    async def get_issue(self, number: int) -> Issue:
        return await self.client.get_issue(self.owner, self.repo, number)

    async def list_pull_files(self, number: int) -> list[PullRequestFile]:
        return await self.client.list_pull_files(self.owner, self.repo, number)

    async def list_pull_commits(self, number: int) -> list[PullRequestCommit]:
        return await self.client.list_pull_commits(self.owner, self.repo, number)

    async def list_pull_reviews(self, number: int) -> list[PullRequestReview]:
        return await self.client.list_pull_reviews(self.owner, self.repo, number)

    async def list_pull_review_comments(self, number: int) -> list[PullRequestReviewComment]:
        return await self.client.list_pull_review_comments(self.owner, self.repo, number)

    async def list_issue_comments(self, number: int) -> list[IssueComment]:
        return await self.client.list_issue_comments(self.owner, self.repo, number)

    async def create_review(
        self,
        number: int,
        *,
        body: str,
        event: ReviewEvent,
        comments: list[ReviewComment] | None = None,
        commit_id: str | None = None,
    ) -> Review:
        return await self.client.create_review(
            self.owner,
            self.repo,
            number,
            body=body,
            event=event,
            comments=comments,
            commit_id=commit_id,
        )

    async def create_issue_comment(self, number: int, *, body: str) -> IssueComment:
        return await self.client.create_issue_comment(self.owner, self.repo, number, body=body)

    async def update_issue_comment(self, comment_id: int, *, body: str) -> IssueComment:
        return await self.client.update_issue_comment(self.owner, self.repo, comment_id, body=body)
