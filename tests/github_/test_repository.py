"""Tests for ``github_.repository`` and the new ``list_branches`` endpoint."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from github_ import GitHubClient, RepositoryHandle

_BASE = "https://api.github.com"


def _client() -> GitHubClient:
    return GitHubClient(token="t0k3n", max_retries=2)


@respx.mock
async def test_list_branches_returns_typed_branches() -> None:
    respx.get(f"{_BASE}/repos/o/r/branches").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "main", "commit": {"sha": "a" * 40}, "protected": True},
                {"name": "dev", "commit": {"sha": "b" * 40}, "protected": False},
            ],
        )
    )

    async with _client() as client:
        branches = await client.list_branches("o", "r")

    assert [b.name for b in branches] == ["main", "dev"]
    assert branches[0].protected is True
    assert branches[0].commit.sha == "a" * 40


def test_from_full_name_parses_owner_repo() -> None:
    client = _client()
    handle = RepositoryHandle.from_full_name("octocat/Hello-World", client)
    assert handle.owner == "octocat"
    assert handle.repo == "Hello-World"
    assert handle.full_name == "octocat/Hello-World"


@pytest.mark.parametrize("bad", ["octocat", "octocat/", "/repo", "a/b/c", "  /  "])
def test_from_full_name_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="owner/repo"):
        RepositoryHandle.from_full_name(bad, _client())


@respx.mock
async def test_handle_delegates_list_pull_requests() -> None:
    route = respx.get(f"{_BASE}/repos/o/r/pulls").mock(return_value=httpx.Response(200, json=[]))

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        result = await handle.list_pull_requests()

    assert result == []
    assert route.called


@respx.mock
async def test_handle_delegates_get_repository() -> None:
    respx.get(f"{_BASE}/repos/o/r").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1,
                "name": "r",
                "full_name": "o/r",
                "default_branch": "main",
                "private": False,
            },
        )
    )

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        repo = await handle.get()

    assert repo.full_name == "o/r"


async def test_handle_delegates_get_file_text() -> None:
    source = b"print('hello')\n"
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["ref"] = request.url.params["ref"]
        return httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(source).decode(),
                "size": len(source),
            },
        )

    async with GitHubClient("token", transport=httpx.MockTransport(handler)) as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        text = await handle.get_file_text("src/task.py", "head-sha", max_bytes=1024)

    assert text == source.decode()
    assert captured == {"path": "/repos/o/r/contents/src/task.py", "ref": "head-sha"}


@respx.mock
async def test_handle_delegates_pull_subresources() -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/7/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 2,
                    "changes": 3,
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/7/commits").mock(
        return_value=httpx.Response(
            200,
            json=[{"sha": "c" * 40, "commit": {"message": "msg"}}],
        )
    )

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        files = await handle.list_pull_files(7)
        commits = await handle.list_pull_commits(7)

    assert files[0].filename == "src/a.py"
    assert commits[0].sha == "c" * 40


@respx.mock
async def test_handle_delegates_create_review() -> None:
    respx.post(f"{_BASE}/repos/o/r/pulls/1/reviews").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 5,
                "body": "ok",
                "state": "COMMENTED",
                "html_url": "https://github.com/o/r/pull/1#review-5",
            },
        )
    )

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        review = await handle.create_review(1, body="ok", event="COMMENT")

    assert review.id == 5
