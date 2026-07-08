"""Pull request aggregator.

One call from a caller turns into a single :class:`PullRequestPayload` that
bundles the PR metadata, every changed file with parsed diff hunks, and the
list of commits on the branch. Downstream code (review agents, the polling
service, the review publisher) consume the payload directly.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from github_.diff import Hunk, parse_patch
from github_.models import Issue, PullRequest, PullRequestCommit, PullRequestFile
from github_.repository import RepositoryHandle

logger = logging.getLogger(__name__)

_ISSUE_REF_TOKEN_RE = re.compile(
    r"https://github\.com/(?P<url_owner>[\w.-]+)/(?P<url_repo>[\w.-]+)/issues/(?P<url_number>\d+)"
    r"|(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+))?#(?P<number>\d+)",
    re.IGNORECASE,
)
_CLOSING_SEGMENT_RE = re.compile(
    r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+"
    r"(?P<refs>(?:https://github\.com/[\w.-]+/[\w.-]+/issues/\d+|"
    r"(?:(?:[\w.-]+/[\w.-]+)?#\d+))"
    r"(?:\s*(?:,|and)\s*"
    r"(?:https://github\.com/[\w.-]+/[\w.-]+/issues/\d+|"
    r"(?:(?:[\w.-]+/[\w.-]+)?#\d+)))*)",
    re.IGNORECASE,
)
_BRANCH_ISSUE_RE = re.compile(
    r"(?:^|[/_-])(?:issue|fix|fixes|close|closes|resolve|resolves)[-_]?(?P<number>\d+)(?:$|[/_-])",
    re.IGNORECASE,
)
_BODY_PREVIEW_CHARS = 500


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
class LinkedIssueReference:
    """A GitHub issue reference found in PR text or branch metadata."""

    owner: str
    repo: str
    number: int
    source: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


@dataclass(frozen=True)
class LinkedIssue:
    """Compact GitHub issue context attached to a pull request."""

    owner: str
    repo: str
    number: int
    title: str
    state: str
    labels: list[str]
    body_preview: str
    url: str
    source: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


@dataclass(frozen=True)
class PullRequestPayload:
    """Everything a review agent needs about one pull request."""

    pull_request: PullRequest
    files: list[ParsedFile]
    commits: list[PullRequestCommit]
    linked_issues: list[LinkedIssue] = field(default_factory=list)

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
        linked_issues = await self._load_linked_issues(pr, commits)
        return PullRequestPayload(
            pull_request=pr,
            files=parsed,
            commits=commits,
            linked_issues=linked_issues,
        )

    async def _load_linked_issues(
        self,
        pr: PullRequest,
        commits: list[PullRequestCommit],
    ) -> list[LinkedIssue]:
        references = _linked_issue_references(
            pr,
            commits,
            default_owner=self._handle.owner,
            default_repo=self._handle.repo,
        )
        if not references:
            return []

        tasks = [self._fetch_linked_issue(ref) for ref in references]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        linked: list[LinkedIssue] = []
        for ref, result in zip(references, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning(
                    "linked issue lookup failed for %s from %s: %s",
                    ref.full_name,
                    ref.source,
                    result,
                )
                continue
            linked.append(result)
        return linked

    async def _fetch_linked_issue(self, ref: LinkedIssueReference) -> LinkedIssue:
        issue = await self._handle.client.get_issue(ref.owner, ref.repo, ref.number)
        return _linked_issue_from_api_issue(issue, ref)


def _linked_issue_references(
    pr: PullRequest,
    commits: list[PullRequestCommit],
    *,
    default_owner: str,
    default_repo: str,
) -> list[LinkedIssueReference]:
    found: dict[tuple[str, str, int], LinkedIssueReference] = {}

    text_sources = [
        ("pull_request.title", pr.title),
        ("pull_request.body", pr.body or ""),
        ("pull_request.head.ref", pr.head.ref),
    ]
    for commit in commits:
        text_sources.append((f"commit:{commit.sha[:12]}", commit.commit.message))

    for source, text in text_sources:
        for ref in _references_from_text(
            str(text or ""),
            source=source,
            default_owner=default_owner,
            default_repo=default_repo,
        ):
            found.setdefault((ref.owner, ref.repo, ref.number), ref)

    branch_ref = pr.head.ref
    match = _BRANCH_ISSUE_RE.search(branch_ref)
    if match:
        number = int(match.group("number"))
        ref = LinkedIssueReference(
            owner=default_owner,
            repo=default_repo,
            number=number,
            source="pull_request.head.ref",
        )
        found.setdefault((ref.owner, ref.repo, ref.number), ref)

    return list(found.values())


def _references_from_text(
    text: str,
    *,
    source: str,
    default_owner: str,
    default_repo: str,
) -> list[LinkedIssueReference]:
    references: list[LinkedIssueReference] = []
    for segment in _CLOSING_SEGMENT_RE.finditer(text):
        ref_text = segment.group("refs")
        for token in _ISSUE_REF_TOKEN_RE.finditer(ref_text):
            references.append(
                _reference_from_match(
                    token,
                    source=source,
                    default_owner=default_owner,
                    default_repo=default_repo,
                )
            )
    return references


def _reference_from_match(
    match: re.Match[str],
    *,
    source: str,
    default_owner: str,
    default_repo: str,
) -> LinkedIssueReference:
    owner = match.group("url_owner") or match.group("owner") or default_owner
    repo = match.group("url_repo") or match.group("repo") or default_repo
    number_text = match.group("url_number") or match.group("number")
    return LinkedIssueReference(
        owner=owner,
        repo=repo,
        number=int(number_text),
        source=source,
    )


def _linked_issue_from_api_issue(issue: Issue, ref: LinkedIssueReference) -> LinkedIssue:
    body = " ".join((issue.body or "").split())
    if len(body) > _BODY_PREVIEW_CHARS:
        body = body[: _BODY_PREVIEW_CHARS - 3].rstrip() + "..."
    return LinkedIssue(
        owner=ref.owner,
        repo=ref.repo,
        number=ref.number,
        title=issue.title,
        state=issue.state,
        labels=[label.name for label in issue.labels],
        body_preview=body,
        url=issue.html_url,
        source=ref.source,
    )
