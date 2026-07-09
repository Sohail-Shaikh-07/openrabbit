"""Async GitHub REST client.

This is the only place in the codebase that talks to ``api.github.com``. Every
other module asks the client for typed objects. Retry behavior, timeout
defaults, and authentication all live here so they cannot drift across
consumers.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import TypeAdapter
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from cli.logging import get_logger
from configs.settings import Settings
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

_log = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 4
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class GitHubAuthError(RuntimeError):
    """Raised when no GitHub token is configured or the token is rejected."""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub returns a non-retryable error response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"GitHub API error {status_code}: {message}")


class _RetryableStatus(Exception):
    """Internal sentinel used to drive tenacity retries on retryable status codes."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"retryable status {status_code}")


_PR_SUMMARIES = TypeAdapter(list[PullRequestSummary])
_PR_FILES = TypeAdapter(list[PullRequestFile])
_PR_COMMITS = TypeAdapter(list[PullRequestCommit])
_BRANCHES = TypeAdapter(list[Branch])
_PR_REVIEWS = TypeAdapter(list[PullRequestReview])
_PR_REVIEW_COMMENTS = TypeAdapter(list[PullRequestReviewComment])
_ISSUE_COMMENTS = TypeAdapter(list[IssueComment])


class GitHubClient:
    """Small async client over the GitHub REST API.

    Construct with :meth:`from_settings` so token resolution stays consistent
    with the rest of the codebase. The instance is an async context manager.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise GitHubAuthError("a GitHub token is required")
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "openrabbit",
            },
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        env: Mapping[str, str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> GitHubClient:
        """Build a client using the token resolution rules from :class:`Settings`."""
        env_map = dict(env) if env is not None else None
        token = settings.resolved_github_token(env=env_map)
        if not token:
            raise GitHubAuthError(
                "no GitHub token found. Set GITHUB_TOKEN, OPENRABBIT_GITHUB__TOKEN, "
                "or the token_env named in .openrabbit/config.yml. On Windows, "
                "OpenRabbit also checks the persistent User/Machine environment; "
                "restart the terminal after setx if needed."
            )
        return cls(
            token=token,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -------- High-level API --------

    async def get_repository(self, owner: str, repo: str) -> Repository:
        data = await self._get(f"/repos/{owner}/{repo}")
        return Repository.model_validate(data)

    async def list_branches(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int = 100,
    ) -> list[Branch]:
        pages = await self._get_paginated(
            f"/repos/{owner}/{repo}/branches",
            params={"per_page": per_page},
        )
        return _BRANCHES.validate_python(pages)

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        state: PullRequestState = "open",
        per_page: int = 100,
    ) -> list[PullRequestSummary]:
        pages = await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": per_page},
        )
        return _PR_SUMMARIES.validate_python(pages)

    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        data = await self._get(f"/repos/{owner}/{repo}/pulls/{number}")
        return PullRequest.model_validate(data)

    async def get_issue(self, owner: str, repo: str, number: int) -> Issue:
        data = await self._get(f"/repos/{owner}/{repo}/issues/{number}")
        return Issue.model_validate(data)

    async def list_pull_files(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        per_page: int = 100,
    ) -> list[PullRequestFile]:
        pages = await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            params={"per_page": per_page},
        )
        return _PR_FILES.validate_python(pages)

    async def list_pull_commits(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        per_page: int = 100,
    ) -> list[PullRequestCommit]:
        pages = await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls/{number}/commits",
            params={"per_page": per_page},
        )
        return _PR_COMMITS.validate_python(pages)

    async def list_pull_reviews(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        per_page: int = 100,
    ) -> list[PullRequestReview]:
        pages = await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            params={"per_page": per_page},
        )
        return _PR_REVIEWS.validate_python(pages)

    async def list_pull_review_comments(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        per_page: int = 100,
    ) -> list[PullRequestReviewComment]:
        pages = await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls/{number}/comments",
            params={"per_page": per_page},
        )
        return _PR_REVIEW_COMMENTS.validate_python(pages)

    async def list_issue_comments(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        per_page: int = 100,
    ) -> list[IssueComment]:
        pages = await self._get_paginated(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            params={"per_page": per_page},
        )
        return _ISSUE_COMMENTS.validate_python(pages)

    async def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        body: str,
        event: ReviewEvent,
        comments: list[ReviewComment] | None = None,
        commit_id: str | None = None,
    ) -> Review:
        payload: dict[str, Any] = {"body": body, "event": event}
        if comments:
            payload["comments"] = [c.model_dump(exclude_none=True) for c in comments]
        if commit_id:
            payload["commit_id"] = commit_id
        data = await self._request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews", json=payload
        )
        return Review.model_validate(data)

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        body: str,
    ) -> IssueComment:
        data = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": body},
        )
        return IssueComment.model_validate(data)

    async def update_issue_comment(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        *,
        body: str,
    ) -> IssueComment:
        data = await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            json={"body": body},
        )
        return IssueComment.model_validate(data)

    # -------- HTTP plumbing --------

    async def _get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _get_paginated(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        results: list[Any] = []
        next_path: str | None = path
        next_params: Mapping[str, Any] | None = params
        while next_path is not None:
            response = await self._request_raw("GET", next_path, params=next_params)
            page = response.json()
            if not isinstance(page, list):
                raise GitHubAPIError(response.status_code, "expected a JSON array")
            results.extend(page)
            next_url = _next_link(response.headers.get("Link", ""))
            if next_url is None:
                next_path = None
            else:
                # Subsequent pages already carry every query parameter on the URL.
                next_path = next_url
                next_params = None
        return results

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        response = await self._request_raw(method, path, params=params, json=json)
        return response.json()

    async def _request_raw(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            retry=retry_if_exception_type((httpx.TransportError, _RetryableStatus)),
            reraise=True,
        ):
            with attempt:
                response = await self._client.request(method, path, params=params, json=json)
                if response.status_code in RETRY_STATUS_CODES:
                    _log.warning(
                        "github.retry",
                        status=response.status_code,
                        method=method,
                        path=path,
                    )
                    raise _RetryableStatus(response.status_code)
                if response.status_code == 401:
                    raise GitHubAuthError("GitHub rejected the configured token")
                if response.status_code >= 400:
                    raise GitHubAPIError(response.status_code, response.text[:500])
                return response
        # Unreachable: tenacity always either returns or raises above.
        raise RuntimeError("retry loop exited without a response")  # pragma: no cover


def _next_link(link_header: str) -> str | None:
    """Parse a GitHub ``Link`` header and return the ``rel="next"`` URL if any."""
    if not link_header:
        return None
    for entry in link_header.split(","):
        entry = entry.strip()
        if not entry.startswith("<"):
            continue
        url, _, rest = entry.partition(">;")
        url = url[1:]
        if 'rel="next"' in rest:
            return url
    return None
