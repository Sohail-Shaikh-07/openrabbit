"""Tests for ``github_.client``."""

from __future__ import annotations

import httpx
import pytest
import respx

from configs import Settings
from github_ import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubClient,
    ReviewComment,
)

_BASE = "https://api.github.com"


def _client() -> GitHubClient:
    # max_retries=2 keeps retry tests fast; production default is higher.
    return GitHubClient(token="t0k3n", max_retries=2)


async def test_constructor_rejects_empty_token() -> None:
    with pytest.raises(GitHubAuthError):
        GitHubClient(token="")


async def test_from_settings_raises_when_no_token_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("configs.settings._persistent_windows_env", lambda name: None)
    settings = Settings()
    with pytest.raises(GitHubAuthError) as exc:
        GitHubClient.from_settings(settings, env={})

    assert "persistent User/Machine environment" in str(exc.value)


async def test_from_settings_uses_resolved_token() -> None:
    settings = Settings()
    client = GitHubClient.from_settings(settings, env={"GITHUB_TOKEN": "abc"})
    assert isinstance(client, GitHubClient)
    await client.aclose()


@respx.mock
async def test_get_repository_returns_typed_repo() -> None:
    route = respx.get(f"{_BASE}/repos/octocat/Hello-World").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1,
                "name": "Hello-World",
                "full_name": "octocat/Hello-World",
                "default_branch": "main",
                "private": False,
                "extra_field_we_ignore": True,
            },
        )
    )
    async with _client() as client:
        repo = await client.get_repository("octocat", "Hello-World")

    assert repo.full_name == "octocat/Hello-World"
    assert repo.default_branch == "main"
    assert route.called


@respx.mock
async def test_authorization_header_uses_bearer_token() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        captured["accept"] = request.headers.get("accept", "")
        return httpx.Response(
            200,
            json={
                "id": 1,
                "name": "r",
                "full_name": "o/r",
                "default_branch": "main",
                "private": False,
            },
        )

    respx.get(f"{_BASE}/repos/o/r").mock(side_effect=handler)

    async with _client() as client:
        await client.get_repository("o", "r")

    assert captured["auth"] == "Bearer t0k3n"
    assert "application/vnd.github" in captured["accept"]


@respx.mock
async def test_retry_on_5xx_then_success() -> None:
    route = respx.get(f"{_BASE}/repos/o/r").mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(
                200,
                json={
                    "id": 1,
                    "name": "r",
                    "full_name": "o/r",
                    "default_branch": "main",
                    "private": False,
                },
            ),
        ]
    )
    async with _client() as client:
        repo = await client.get_repository("o", "r")

    assert repo.name == "r"
    assert route.call_count == 2


@respx.mock
async def test_retry_gives_up_and_raises() -> None:
    """After max_retries the last retryable response should surface as an error."""
    from github_.client import _RetryableStatus

    respx.get(f"{_BASE}/repos/o/r").mock(return_value=httpx.Response(503, text="still busy"))
    async with _client() as client:
        with pytest.raises(_RetryableStatus):
            await client.get_repository("o", "r")


@respx.mock
async def test_401_raises_auth_error() -> None:
    respx.get(f"{_BASE}/repos/o/r").mock(return_value=httpx.Response(401, text="bad token"))
    async with _client() as client:
        with pytest.raises(GitHubAuthError):
            await client.get_repository("o", "r")


@respx.mock
async def test_404_raises_api_error_with_status() -> None:
    respx.get(f"{_BASE}/repos/o/r").mock(return_value=httpx.Response(404, text="no such repo"))
    async with _client() as client:
        with pytest.raises(GitHubAPIError) as exc:
            await client.get_repository("o", "r")

    assert exc.value.status_code == 404


@respx.mock
async def test_list_pull_requests_handles_pagination() -> None:
    page1 = [
        {
            "number": 1,
            "title": "first",
            "state": "open",
            "draft": False,
            "user": {"login": "alice", "id": 100},
            "head": {"ref": "feat", "sha": "a" * 40, "label": "alice:feat"},
            "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "labels": [],
        }
    ]
    page2 = [
        {
            "number": 2,
            "title": "second",
            "state": "open",
            "draft": False,
            "user": {"login": "bob", "id": 200},
            "head": {"ref": "feat2", "sha": "c" * 40, "label": "bob:feat2"},
            "base": {"ref": "main", "sha": "d" * 40, "label": "o:main"},
            "created_at": "2026-01-03T00:00:00Z",
            "updated_at": "2026-01-04T00:00:00Z",
            "labels": [],
        }
    ]

    # Order matters in respx: it tries the most-recently-registered route first.
    # Register the page-2 route before the page-1 (catch-all) route so the second
    # request does not bounce back to the same page.
    respx.get(f"{_BASE}/repos/o/r/pulls", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=page2)
    )
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            200,
            json=page1,
            headers={"Link": f'<{_BASE}/repos/o/r/pulls?page=2>; rel="next"'},
        )
    )

    async with _client() as client:
        prs = await client.list_pull_requests("o", "r")

    assert [pr.number for pr in prs] == [1, 2]


@respx.mock
async def test_get_issue_returns_compact_issue_metadata() -> None:
    respx.get(f"{_BASE}/repos/o/r/issues/12").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 12,
                "title": "Search endpoint should be safe",
                "state": "open",
                "body": "Users can search tasks by title.",
                "labels": [{"name": "security"}, {"name": "api"}],
                "html_url": "https://github.com/o/r/issues/12",
            },
        )
    )

    async with _client() as client:
        issue = await client.get_issue("o", "r", 12)

    assert issue.number == 12
    assert issue.title == "Search endpoint should be safe"
    assert [label.name for label in issue.labels] == ["security", "api"]


@respx.mock
async def test_create_review_sends_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": 99,
                "body": "looks good",
                "state": "COMMENTED",
                "html_url": "https://github.com/o/r/pull/1#review-99",
                "submitted_at": "2026-01-01T00:00:00Z",
            },
        )

    respx.post(f"{_BASE}/repos/o/r/pulls/1/reviews").mock(side_effect=handler)

    async with _client() as client:
        review = await client.create_review(
            "o",
            "r",
            1,
            body="looks good",
            event="COMMENT",
            comments=[ReviewComment(path="src/a.py", body="nit", line=3, side="RIGHT")],
        )

    assert review.id == 99
    assert "looks good" in str(captured["body"])
    assert "src/a.py" in str(captured["body"])


@respx.mock
async def test_create_issue_comment_sends_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": 77,
                "user": {"login": "openrabbit", "id": 42},
                "body": "answer body",
                "html_url": "https://github.com/o/r/pull/1#issuecomment-77",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        )

    respx.post(f"{_BASE}/repos/o/r/issues/1/comments").mock(side_effect=handler)

    async with _client() as client:
        comment = await client.create_issue_comment("o", "r", 1, body="answer body")

    assert comment.id == 77
    assert "answer body" in str(captured["body"])


@respx.mock
async def test_update_issue_comment_sends_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": 77,
                "user": {"login": "openrabbit", "id": 42},
                "body": "updated body",
                "html_url": "https://github.com/o/r/pull/1#issuecomment-77",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:05:00Z",
            },
        )

    respx.patch(f"{_BASE}/repos/o/r/issues/comments/77").mock(side_effect=handler)

    async with _client() as client:
        comment = await client.update_issue_comment("o", "r", 77, body="updated body")

    assert comment.id == 77
    assert "updated body" in str(captured["body"])


@respx.mock
async def test_list_pull_conversation_sources_returns_typed_objects() -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/1/reviews").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 10,
                    "user": {"login": "reviewer", "id": 2},
                    "body": "Please fix the query.",
                    "state": "COMMENTED",
                    "commit_id": "a" * 40,
                    "submitted_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://github.com/o/r/pull/1#pullrequestreview-10",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/1/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 20,
                    "user": {"login": "reviewer", "id": 2},
                    "body": "This line is unsafe.",
                    "path": "src/a.py",
                    "line": 2,
                    "commit_id": "a" * 40,
                    "created_at": "2026-01-01T00:01:00Z",
                    "updated_at": "2026-01-01T00:02:00Z",
                    "html_url": "https://github.com/o/r/pull/1#discussion_r20",
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/issues/1/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 30,
                    "user": {"login": "author", "id": 3},
                    "body": "Fixed in the latest commit.",
                    "created_at": "2026-01-01T00:03:00Z",
                    "updated_at": "2026-01-01T00:04:00Z",
                    "html_url": "https://github.com/o/r/pull/1#issuecomment-30",
                }
            ],
        )
    )

    async with _client() as client:
        reviews = await client.list_pull_reviews("o", "r", 1)
        review_comments = await client.list_pull_review_comments("o", "r", 1)
        issue_comments = await client.list_issue_comments("o", "r", 1)

    assert reviews[0].body == "Please fix the query."
    assert reviews[0].user.login == "reviewer"
    assert review_comments[0].path == "src/a.py"
    assert review_comments[0].line == 2
    assert issue_comments[0].body == "Fixed in the latest commit."
