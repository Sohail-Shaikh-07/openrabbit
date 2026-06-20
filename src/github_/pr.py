"""Pull request aggregator.

One call from a caller turns into a single :class:`PullRequestPayload` that
bundles the PR metadata, every changed file with parsed diff hunks, and the
list of commits on the branch. Downstream code (review agents, the polling
service, the review publisher) consume the payload directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from github_.diff import Hunk, parse_patch
from github_.models import PullRequest, PullRequestCommit, PullRequestFile
from github_.repository import RepositoryHandle


@dataclass(frozen=True)
class ParsedFile:
    """A pull request file with its diff parsed into structured hunks."""

    file: PullRequestFile
    hunks: list[Hunk]

    @property
    def path(self) -> str:
        return self.file.filename

    @property
    def status(self) -> str:
        return self.file.status

    @property
    def is_binary(self) -> bool:
        """True when GitHub did not return a patch.

        Binary files, renames with no content change, and very large files all
        end up here. The agents should usually skip these.
        """
        return self.file.patch is None


@dataclass(frozen=True)
class PullRequestPayload:
    """Everything a review agent needs about one pull request."""

    pull_request: PullRequest
    files: list[ParsedFile]
    commits: list[PullRequestCommit]

    @property
    def number(self) -> int:
        return self.pull_request.number

    @property
    def head_sha(self) -> str:
        return self.pull_request.head.sha


class PullRequestParser:
    """Fetches and stitches together the data needed to review one PR."""

    def __init__(self, handle: RepositoryHandle) -> None:
        self._handle = handle

    async def parse(self, number: int) -> PullRequestPayload:
        """Fetch the PR, its files, and its commits concurrently and combine them."""
        pr, files, commits = await asyncio.gather(
            self._handle.get_pull_request(number),
            self._handle.list_pull_files(number),
            self._handle.list_pull_commits(number),
        )
        parsed = [ParsedFile(file=f, hunks=parse_patch(f.patch)) for f in files]
        return PullRequestPayload(pull_request=pr, files=parsed, commits=commits)
