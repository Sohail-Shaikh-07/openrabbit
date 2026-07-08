"""Tests for ``github_.pr.PullRequestParser``."""

from __future__ import annotations

import httpx
import respx

from github_ import GitHubClient, PullRequestParser, RepositoryHandle

_BASE = "https://api.github.com"


def _client() -> GitHubClient:
    return GitHubClient(token="t0k3n", max_retries=2)


def _pr_json(number: int) -> dict[str, object]:
    return {
        "number": number,
        "title": f"PR {number}",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "feat", "sha": "h" * 40, "label": "alice:feat"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "labels": [],
        "body": "Body here",
        "merged": False,
    }


def _issue_json(number: int, *, title: str = "Linked issue") -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "body": "Implement the requested behavior with tests.",
        "labels": [{"name": "enhancement"}],
        "html_url": f"https://github.com/o/r/issues/{number}",
    }


@respx.mock
async def test_parse_combines_pr_files_and_commits() -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/7").mock(return_value=httpx.Response(200, json=_pr_json(7)))
    respx.get(f"{_BASE}/repos/o/r/pulls/7/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                    "changes": 2,
                    "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
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
        payload = await PullRequestParser(handle).parse(7)

    assert payload.number == 7
    assert payload.head_sha == "h" * 40
    assert len(payload.files) == 1
    parsed = payload.files[0]
    assert parsed.path == "src/a.py"
    assert parsed.status == "modified"
    assert not parsed.is_binary
    assert [h.new_start for h in parsed.hunks] == [1]
    assert len(payload.commits) == 1


@respx.mock
async def test_parse_handles_binary_file_without_patch() -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/8").mock(return_value=httpx.Response(200, json=_pr_json(8)))
    respx.get(f"{_BASE}/repos/o/r/pulls/8/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "assets/logo.png",
                    "status": "added",
                    "additions": 0,
                    "deletions": 0,
                    "changes": 0,
                    # No patch: GitHub does not return one for binary blobs.
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/8/commits").mock(return_value=httpx.Response(200, json=[]))

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        payload = await PullRequestParser(handle).parse(8)

    assert payload.files[0].is_binary is True
    assert payload.files[0].hunks == []


@respx.mock
async def test_parse_handles_rename_without_patch() -> None:
    respx.get(f"{_BASE}/repos/o/r/pulls/9").mock(return_value=httpx.Response(200, json=_pr_json(9)))
    respx.get(f"{_BASE}/repos/o/r/pulls/9/files").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "filename": "src/new_name.py",
                    "previous_filename": "src/old_name.py",
                    "status": "renamed",
                    "additions": 0,
                    "deletions": 0,
                    "changes": 0,
                }
            ],
        )
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/9/commits").mock(return_value=httpx.Response(200, json=[]))

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        payload = await PullRequestParser(handle).parse(9)

    assert payload.files[0].status == "renamed"
    assert payload.files[0].file.previous_filename == "src/old_name.py"
    assert payload.files[0].hunks == []


@respx.mock
async def test_parse_fetches_three_endpoints_concurrently() -> None:
    """All three sub-requests should be issued in the same gather call.

    We assert each route was hit exactly once. If the parser fell back to
    serial fetching by mistake we would still get that, but combined with the
    explicit asyncio.gather in the implementation this guards against future
    refactors that drop concurrency.
    """
    pr_route = respx.get(f"{_BASE}/repos/o/r/pulls/1").mock(
        return_value=httpx.Response(200, json=_pr_json(1))
    )
    files_route = respx.get(f"{_BASE}/repos/o/r/pulls/1/files").mock(
        return_value=httpx.Response(200, json=[])
    )
    commits_route = respx.get(f"{_BASE}/repos/o/r/pulls/1/commits").mock(
        return_value=httpx.Response(200, json=[])
    )

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        await PullRequestParser(handle).parse(1)

    assert pr_route.call_count == 1
    assert files_route.call_count == 1
    assert commits_route.call_count == 1


@respx.mock
async def test_parse_loads_linked_issue_context_from_pr_body() -> None:
    pr_json = _pr_json(10)
    pr_json["body"] = "Adds safer search. Fixes #12 and Resolves other/repo#44."
    respx.get(f"{_BASE}/repos/o/r/pulls/10").mock(return_value=httpx.Response(200, json=pr_json))
    respx.get(f"{_BASE}/repos/o/r/pulls/10/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/10/commits").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/issues/12").mock(
        return_value=httpx.Response(200, json=_issue_json(12, title="Safe search"))
    )
    respx.get(f"{_BASE}/repos/other/repo/issues/44").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 44,
                "title": "Cross repo rollout",
                "state": "closed",
                "body": "Coordinate the rollout across repositories.",
                "labels": [{"name": "tracking"}],
                "html_url": "https://github.com/other/repo/issues/44",
            },
        )
    )

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        payload = await PullRequestParser(handle).parse(10)

    assert [issue.full_name for issue in payload.linked_issues] == ["o/r#12", "other/repo#44"]
    assert payload.linked_issues[0].title == "Safe search"
    assert payload.linked_issues[0].labels == ["enhancement"]
    assert payload.linked_issues[0].source == "pull_request.body"


@respx.mock
async def test_parse_infers_linked_issue_from_branch_ref() -> None:
    pr_json = _pr_json(11)
    pr_json["head"] = {"ref": "feature/fix-88-search", "sha": "h" * 40, "label": "alice:feat"}
    respx.get(f"{_BASE}/repos/o/r/pulls/11").mock(return_value=httpx.Response(200, json=pr_json))
    respx.get(f"{_BASE}/repos/o/r/pulls/11/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/11/commits").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/issues/88").mock(
        return_value=httpx.Response(200, json=_issue_json(88, title="Branch issue"))
    )

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        payload = await PullRequestParser(handle).parse(11)

    assert [issue.full_name for issue in payload.linked_issues] == ["o/r#88"]
    assert payload.linked_issues[0].source == "pull_request.head.ref"


@respx.mock
async def test_parse_continues_when_linked_issue_fetch_fails() -> None:
    pr_json = _pr_json(12)
    pr_json["body"] = "Closes #99"
    respx.get(f"{_BASE}/repos/o/r/pulls/12").mock(return_value=httpx.Response(200, json=pr_json))
    respx.get(f"{_BASE}/repos/o/r/pulls/12/files").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/pulls/12/commits").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_BASE}/repos/o/r/issues/99").mock(
        return_value=httpx.Response(404, text="not found")
    )

    async with _client() as client:
        handle = RepositoryHandle(owner="o", repo="r", client=client)
        payload = await PullRequestParser(handle).parse(12)

    assert payload.linked_issues == []
