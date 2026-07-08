"""Tests for ``cli.commands.start``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from cli.commands.init import run_init
from cli.commands.start import StartError, resolve_target_repo, run_start
from configs import PollingSettings, RepositorySettings, Settings, load_settings
from github_ import FileStateStore, GitHubClient, PollEvent, PollState, SeenPullRequest
from github_.models import PullRequestSummary
from github_.pr_commands import InMemoryCommandStateStore
from github_.repository import RepositoryHandle
from memory.store import SQLitePullRequestMemory

_BASE = "https://api.github.com"


def _pr_summary(
    number: int,
    *,
    updated_at: str,
    head_sha: str,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"PR {number}",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "id": 1},
        "head": {"ref": "feat", "sha": head_sha, "label": "alice:feat"},
        "base": {"ref": "main", "sha": "b" * 40, "label": "o:main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": updated_at,
        "labels": [],
    }


def _seed_state(scaffold_repo: Path, *prs: SeenPullRequest) -> None:
    FileStateStore(scaffold_repo / ".openrabbit" / "state.json").save(
        PollState(pull_requests={pr.number: pr for pr in prs})
    )


def _issue_comment(comment_id: int, body: str) -> dict[str, Any]:
    return {
        "id": comment_id,
        "user": {"login": "alice", "id": 1},
        "body": body,
        "html_url": f"https://github.com/o/r/pull/1#issuecomment-{comment_id}",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _pull_file(filename: str) -> dict[str, Any]:
    return {
        "sha": "a" * 40,
        "filename": filename,
        "status": "modified",
        "additions": 1,
        "deletions": 0,
        "changes": 1,
        "patch": "@@ -1 +1 @@\n-old\n+new",
    }


def _event(kind: str, number: int = 1) -> PollEvent:
    return PollEvent(
        kind=kind,  # type: ignore[arg-type]
        pull_request=PullRequestSummary.model_validate(
            _pr_summary(number, updated_at="2026-01-03T00:00:00Z", head_sha="a" * 40)
        ),
    )


def test_resolve_target_flag_wins_over_settings() -> None:
    settings = Settings(repository=RepositorySettings(target="from/settings"))
    assert resolve_target_repo(settings, "from/flag") == "from/flag"


def test_resolve_target_falls_back_to_settings() -> None:
    settings = Settings(repository=RepositorySettings(target="from/settings"))
    assert resolve_target_repo(settings, None) == "from/settings"


def test_resolve_target_missing_both_raises() -> None:
    with pytest.raises(StartError, match="no repository"):
        resolve_target_repo(Settings(), None)


@respx.mock
async def test_run_start_runs_polling_until_cancelled(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single failing GitHub call inside run_forever should not crash the loop.

    We stop the loop by patching asyncio.sleep to raise CancelledError after the
    first round, which is the same way a Ctrl-C aborts the daemon.
    """
    import asyncio

    rounds: list[int] = []

    async def fake_sleep(_seconds: float) -> None:
        rounds.append(1)
        raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(return_value=httpx.Response(200, json=[]))

    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(asyncio.CancelledError):
        await run_start(
            settings,
            workspace=scaffold_repo,
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
        )

    assert rounds == [1]
    assert (scaffold_repo / ".openrabbit" / "state.json").is_file()


@respx.mock
async def test_run_start_reviews_new_pull_request(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    _seed_state(
        scaffold_repo,
        SeenPullRequest(
            number=1,
            updated_at="2026-01-01T00:00:00+00:00",
            head_sha="a" * 40,
        ),
    )
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                _pr_summary(1, updated_at="2026-01-01T00:00:00Z", head_sha="a" * 40),
                _pr_summary(2, updated_at="2026-01-02T00:00:00Z", head_sha="c" * 40),
            ],
        )
    )
    reviewed: list[int] = []

    async def fake_review_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        reviewed.append(int(kwargs["number"]))
        return {"findings_count": 1, "comments_posted": True}

    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(asyncio.CancelledError):
        await run_start(
            settings,
            workspace=scaffold_repo,
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
            review_runner=fake_review_runner,
        )

    assert reviewed == [2]


@respx.mock
async def test_run_start_reviews_new_head_sha(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    _seed_state(
        scaffold_repo,
        SeenPullRequest(
            number=1,
            updated_at="2026-01-01T00:00:00+00:00",
            head_sha="a" * 40,
        ),
    )
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[_pr_summary(1, updated_at="2026-01-02T00:00:00Z", head_sha="z" * 40)],
        )
    )
    reviewed: list[int] = []

    async def fake_review_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        reviewed.append(int(kwargs["number"]))
        return {"findings_count": 1, "comments_posted": True}

    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(asyncio.CancelledError):
        await run_start(
            settings,
            workspace=scaffold_repo,
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
            review_runner=fake_review_runner,
        )

    assert reviewed == [1]


@respx.mock
async def test_run_start_skips_same_head_sha_update(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    _seed_state(
        scaffold_repo,
        SeenPullRequest(
            number=1,
            updated_at="2026-01-01T00:00:00+00:00",
            head_sha="a" * 40,
        ),
    )
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[_pr_summary(1, updated_at="2026-01-03T00:00:00Z", head_sha="a" * 40)],
        )
    )
    reviewed: list[int] = []

    async def fake_review_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        reviewed.append(int(kwargs["number"]))
        return {"findings_count": 1, "comments_posted": True}

    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(asyncio.CancelledError):
        await run_start(
            settings,
            workspace=scaffold_repo,
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
            review_runner=fake_review_runner,
        )

    assert reviewed == []


@respx.mock
async def test_start_command_listener_runs_pr_comment_commands(scaffold_repo: Path) -> None:
    from cli.commands.start import build_review_handler

    respx.get(f"{_BASE}/repos/o/r/issues/1/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                _issue_comment(10, "@openrabbit review"),
                _issue_comment(11, "@openrabbit full review"),
                _issue_comment(12, "@openrabbit improve"),
                _issue_comment(13, "@openrabbit ask what changed?"),
                _issue_comment(14, "@openrabbit learn Prefer bind parameters for SQL."),
            ],
        )
    )
    review_calls: list[dict[str, object]] = []
    improve_calls: list[dict[str, object]] = []
    ask_calls: list[dict[str, object]] = []
    replies: list[str] = []

    async def fake_review_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        review_calls.append(kwargs)
        return {"findings_count": 0, "comments_posted": False}

    async def fake_improve_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        improve_calls.append(kwargs)
        return {"suggestions_count": 1, "publish_status": "posted"}

    async def fake_ask_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        ask_calls.append(kwargs)
        return {"answer": {"answer": "It updates search."}}

    async def fake_reply_publisher(**kwargs: object) -> None:
        replies.append(str(kwargs["body"]))

    settings = load_settings(scaffold_repo, env={})
    command_store = InMemoryCommandStateStore()
    handler = build_review_handler(
        settings,
        env={"GITHUB_TOKEN": "tkn"},
        review_runner=fake_review_runner,
        improve_runner=fake_improve_runner,
        ask_runner=fake_ask_runner,
        command_store=command_store,
        issue_comment_publisher=fake_reply_publisher,
    )
    client = GitHubClient(token="tkn")
    handle = RepositoryHandle(owner="o", repo="r", client=client)

    try:
        await handler(_event("pull_request_updated"), handle)
    finally:
        await client.aclose()

    assert [call.get("mode") for call in review_calls] == ["incremental", "full"]
    assert improve_calls[0]["publish"] is True
    assert ask_calls[0]["question"] == "what changed?"
    assert "It updates search." in replies[0]
    assert command_store.load().last_seen_comment_id(1) == 14
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    assert store.list_learnings("o/r")[0].instruction == "Prefer bind parameters for SQL."


@respx.mock
async def test_start_command_listener_respects_pause_and_resume(scaffold_repo: Path) -> None:
    from cli.commands.start import build_review_handler

    settings = load_settings(scaffold_repo, env={})
    command_store = InMemoryCommandStateStore()
    reviewed: list[int] = []

    async def fake_review_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        reviewed.append(int(kwargs["number"]))
        return {"findings_count": 0, "comments_posted": False}

    handler = build_review_handler(
        settings,
        env={"GITHUB_TOKEN": "tkn"},
        review_runner=fake_review_runner,
        command_store=command_store,
    )
    client = GitHubClient(token="tkn")
    handle = RepositoryHandle(owner="o", repo="r", client=client)

    try:
        respx.get(f"{_BASE}/repos/o/r/issues/1/comments").mock(
            return_value=httpx.Response(200, json=[_issue_comment(20, "@openrabbit pause")])
        )
        await handler(_event("pull_request_updated"), handle)
        await handler(_event("commit_pushed"), handle)
        assert reviewed == []
        assert command_store.load().is_paused(1)

        respx.get(f"{_BASE}/repos/o/r/issues/1/comments").mock(
            return_value=httpx.Response(200, json=[_issue_comment(21, "@openrabbit resume")])
        )
        await handler(_event("pull_request_updated"), handle)
        await handler(_event("commit_pushed"), handle)
    finally:
        await client.aclose()

    assert reviewed == [1]
    assert not command_store.load().is_paused(1)


async def test_start_handler_skips_reviews_during_cooldown(scaffold_repo: Path) -> None:
    from cli.commands.start import build_review_handler

    settings = load_settings(scaffold_repo, env={})
    settings.polling = PollingSettings(review_cooldown_seconds=300)
    reviewed: list[int] = []

    async def fake_review_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        reviewed.append(int(kwargs["number"]))
        return {"findings_count": 0, "comments_posted": False}

    handler = build_review_handler(
        settings,
        env={"GITHUB_TOKEN": "tkn"},
        review_runner=fake_review_runner,
    )
    client = GitHubClient(token="tkn")
    handle = RepositoryHandle(owner="o", repo="r", client=client)

    try:
        await handler(_event("commit_pushed"), handle)
        await handler(_event("commit_pushed"), handle)
    finally:
        await client.aclose()

    assert reviewed == [1]


@respx.mock
async def test_start_handler_skips_prs_over_changed_file_limit(scaffold_repo: Path) -> None:
    from cli.commands.start import build_review_handler

    settings = load_settings(scaffold_repo, env={})
    settings.polling = PollingSettings(max_changed_files=1)
    reviewed: list[int] = []

    async def fake_review_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        reviewed.append(int(kwargs["number"]))
        return {"findings_count": 0, "comments_posted": False}

    handler = build_review_handler(
        settings,
        env={"GITHUB_TOKEN": "tkn"},
        review_runner=fake_review_runner,
    )
    respx.get(f"{_BASE}/repos/o/r/pulls/1/files").mock(
        return_value=httpx.Response(
            200,
            json=[_pull_file("src/a.py"), _pull_file("src/b.py")],
        )
    )
    client = GitHubClient(token="tkn")
    handle = RepositoryHandle(owner="o", repo="r", client=client)

    try:
        await handler(_event("commit_pushed"), handle)
    finally:
        await client.aclose()

    assert reviewed == []


@respx.mock
async def test_run_start_review_failure_does_not_stop_polling(
    scaffold_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    rounds: list[int] = []

    async def fake_sleep(_seconds: float) -> None:
        rounds.append(1)
        raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    _seed_state(
        scaffold_repo,
        SeenPullRequest(
            number=1,
            updated_at="2026-01-01T00:00:00+00:00",
            head_sha="a" * 40,
        ),
    )
    respx.get(f"{_BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[_pr_summary(1, updated_at="2026-01-02T00:00:00Z", head_sha="z" * 40)],
        )
    )

    async def fake_review_runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("review failed")

    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(asyncio.CancelledError):
        await run_start(
            settings,
            workspace=scaffold_repo,
            repo="o/r",
            env={"GITHUB_TOKEN": "tkn"},
            review_runner=fake_review_runner,
        )

    assert rounds == [1]


def test_run_start_blocking_raises_start_error_when_no_repo(
    scaffold_repo: Path,
) -> None:
    settings = load_settings(scaffold_repo, env={})

    with pytest.raises(StartError):
        resolve_target_repo(settings, None)


def test_run_init_template_sets_no_repository_target(scaffold_repo: Path) -> None:
    """The init template ships with repository.target commented out.

    A fresh `openrabbit init` followed by `openrabbit start` must hit the
    no-repo guard rather than wiring to a phantom default.
    """
    run_init(scaffold_repo, force=True)
    settings = load_settings(scaffold_repo, env={})
    assert settings.repository.target is None
