"""Implementation of ``openrabbit start``.

Wires settings, GitHub access, polling state, and the review pipeline into one
foreground daemon. The daemon reviews new pull requests and new head commits,
while same-SHA metadata updates are logged and skipped.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from io import StringIO
from pathlib import Path
from typing import Any

from cli.commands.pr_summary import publish_pr_summary_body
from cli.logging import get_logger
from configs.settings import Settings
from github_ import (
    CommandStateStore,
    FileCommandStateStore,
    FileStateStore,
    GitHubClient,
    InMemoryCommandStateStore,
    PollEvent,
    PollingService,
    RepositoryHandle,
    parse_openrabbit_command,
)
from memory.store import SQLitePullRequestMemory

_log = get_logger(__name__)

STATE_SUBDIR = ".openrabbit"
STATE_FILENAME = "state.json"
COMMAND_STATE_FILENAME = "commands.json"
ReviewRunner = Callable[..., Awaitable[dict[str, object]]]
ImproveRunner = Callable[..., Awaitable[dict[str, object]]]
AskRunner = Callable[..., Awaitable[dict[str, object]]]
DescribeRunner = Callable[..., Awaitable[dict[str, object]]]
IssueCommentPublisher = Callable[..., Awaitable[None]]


class StartError(RuntimeError):
    """Raised when the start command cannot be wired up from settings."""


def format_start_banner(repo: str, interval: int, ver: str) -> str:
    """Return a one-line startup banner string for ``openrabbit start``."""
    return f"OpenRabbit {ver} | watching {repo} | polling every {interval}s"


def resolve_target_repo(settings: Settings, flag: str | None) -> str:
    """Pick the target repo. CLI flag wins over the setting; raise if neither set."""
    if flag:
        return flag
    if settings.repository.target:
        return settings.repository.target
    raise StartError(
        "no repository to watch. Pass --repo OWNER/REPO or set repository.target "
        "in .openrabbit/config.yml."
    )


async def _log_handler(event: PollEvent, handle: RepositoryHandle) -> None:
    _log.info(
        "polling.event",
        repo=handle.full_name,
        kind=event.kind,
        pr=event.number,
        title=event.pull_request.title,
    )


async def _run_manual_review(*args: Any, **kwargs: Any) -> dict[str, object]:
    """Import lazily to avoid a cycle with ``cli.commands.review``."""
    from cli.commands.review import run_review

    return await run_review(*args, **kwargs)


async def _run_manual_improve(*args: Any, **kwargs: Any) -> dict[str, object]:
    """Import lazily to avoid a cycle with ``cli.commands.improve``."""
    from cli.commands.improve import run_improve

    return await run_improve(*args, **kwargs)


async def _run_manual_ask(*args: Any, **kwargs: Any) -> dict[str, object]:
    """Import lazily to avoid a cycle with ``cli.commands.ask``."""
    from cli.commands.ask import run_ask

    return await run_ask(*args, **kwargs)


async def _run_manual_describe(*args: Any, **kwargs: Any) -> dict[str, object]:
    """Import lazily to avoid a cycle with ``cli.commands.describe``."""
    from cli.commands.describe import run_describe

    return await run_describe(*args, **kwargs)


def build_review_handler(
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
    improve_runner: ImproveRunner | None = None,
    ask_runner: AskRunner | None = None,
    describe_runner: DescribeRunner | None = None,
    command_store: CommandStateStore | None = None,
    issue_comment_publisher: IssueCommentPublisher | None = None,
) -> Callable[[PollEvent, RepositoryHandle], Awaitable[None]]:
    """Build the polling handler that reviews PRs and handles PR commands."""
    review = review_runner or _run_manual_review
    improve = improve_runner or _run_manual_improve
    ask = ask_runner or _run_manual_ask
    describe = describe_runner or _run_manual_describe
    commands = command_store or InMemoryCommandStateStore()
    review_started_at: dict[int, float] = {}

    async def _handler(event: PollEvent, handle: RepositoryHandle) -> None:
        await _log_handler(event, handle)
        if event.kind == "pull_request_updated":
            handled = await _handle_pr_commands(
                settings,
                event=event,
                handle=handle,
                env=env,
                command_store=commands,
                review_runner=review,
                improve_runner=improve,
                ask_runner=ask,
                describe_runner=describe,
                issue_comment_publisher=issue_comment_publisher,
            )
            if not handled:
                _log.info(
                    "start.review_skipped",
                    repo=handle.full_name,
                    pr=event.number,
                    reason="head_sha_unchanged",
                )
            return

        state = commands.load()
        if state.is_ignored(event.number):
            _log.info(
                "start.review_skipped",
                repo=handle.full_name,
                pr=event.number,
                reason="openrabbit_ignored",
            )
            return

        if state.is_paused(event.number):
            _log.info(
                "start.review_skipped",
                repo=handle.full_name,
                pr=event.number,
                reason="openrabbit_paused",
            )
            return

        cooldown_remaining = _cooldown_remaining(
            event.number,
            review_started_at=review_started_at,
            now=time.monotonic(),
            cooldown_seconds=settings.polling.review_cooldown_seconds,
        )
        if cooldown_remaining > 0:
            _log.info(
                "start.review_skipped",
                repo=handle.full_name,
                pr=event.number,
                reason="review_cooldown",
                cooldown_remaining_seconds=round(cooldown_remaining, 3),
            )
            return

        if await _should_skip_for_changed_files(settings, event=event, handle=handle):
            return

        review_started_at[event.number] = time.monotonic()
        _log.info(
            "start.review_started",
            repo=handle.full_name,
            pr=event.number,
            event_kind=event.kind,
            head_sha=event.pull_request.head.sha,
        )
        try:
            summary = await review(
                settings,
                number=event.number,
                repo=handle.full_name,
                env=env,
                dry_run=False,
                mode="incremental",
            )
        except Exception as exc:
            _log.error(
                "start.review_failed",
                repo=handle.full_name,
                pr=event.number,
                event_kind=event.kind,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

        _log.info(
            "start.review_complete",
            repo=handle.full_name,
            pr=event.number,
            findings=summary.get("findings_count", 0),
            comments_posted=summary.get("comments_posted", False),
            publish_status=summary.get("publish_status"),
            published_findings=summary.get("published_findings_count", 0),
            context_loaded=summary.get("context_loaded"),
            memory_context=summary.get("memory_context"),
            skipped_paths=summary.get("skipped_paths_count", 0),
        )

    return _handler


def _cooldown_remaining(
    pr_number: int,
    *,
    review_started_at: dict[int, float],
    now: float,
    cooldown_seconds: int,
) -> float:
    if cooldown_seconds <= 0:
        return 0.0
    previous = review_started_at.get(pr_number)
    if previous is None:
        return 0.0
    return max(0.0, cooldown_seconds - (now - previous))


async def _should_skip_for_changed_files(
    settings: Settings,
    *,
    event: PollEvent,
    handle: RepositoryHandle,
) -> bool:
    limit = settings.polling.max_changed_files
    if limit is None:
        return False
    try:
        changed_files = len(await handle.list_pull_files(event.number))
    except Exception as exc:
        _log.warning(
            "start.size_check_failed",
            repo=handle.full_name,
            pr=event.number,
            max_changed_files=limit,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
    if changed_files <= limit:
        return False
    _log.info(
        "start.review_skipped",
        repo=handle.full_name,
        pr=event.number,
        reason="changed_files_limit_exceeded",
        changed_files=changed_files,
        max_changed_files=limit,
    )
    return True


async def _handle_pr_commands(
    settings: Settings,
    *,
    event: PollEvent,
    handle: RepositoryHandle,
    env: dict[str, str] | None,
    command_store: CommandStateStore,
    review_runner: ReviewRunner,
    improve_runner: ImproveRunner,
    ask_runner: AskRunner,
    describe_runner: DescribeRunner,
    issue_comment_publisher: IssueCommentPublisher | None,
) -> bool:
    state = command_store.load()
    last_seen = state.last_seen_comment_id(event.number)
    comments = sorted(await handle.list_issue_comments(event.number), key=lambda item: item.id)
    handled = False

    for comment in comments:
        if comment.id <= last_seen:
            continue
        command = parse_openrabbit_command(comment.body)
        state = state.mark_comment_seen(event.number, comment.id)
        command_store.save(state)
        if command is None:
            continue
        handled = True
        if command.kind == "pause":
            state = state.pause(event.number)
            command_store.save(state)
            _log.info("start.command_pause", repo=handle.full_name, pr=event.number)
            continue
        if command.kind == "resume":
            state = state.resume(event.number)
            command_store.save(state)
            _log.info("start.command_resume", repo=handle.full_name, pr=event.number)
            continue
        if command.kind == "ignore":
            state = state.ignore(event.number)
            command_store.save(state)
            _log.info("start.command_ignore", repo=handle.full_name, pr=event.number)
            continue
        if state.is_ignored(event.number) or state.is_paused(event.number):
            reason = "openrabbit_ignored" if state.is_ignored(event.number) else "openrabbit_paused"
            _log.info(
                "start.command_ignored",
                repo=handle.full_name,
                pr=event.number,
                command=command.kind,
                reason=reason,
            )
            continue
        if command.kind == "review":
            await review_runner(
                settings,
                number=event.number,
                repo=handle.full_name,
                env=env,
                dry_run=False,
                mode="incremental",
            )
        elif command.kind == "full_review":
            await review_runner(
                settings,
                number=event.number,
                repo=handle.full_name,
                env=env,
                dry_run=False,
                mode="full",
            )
        elif command.kind == "improve":
            await improve_runner(
                settings,
                number=event.number,
                repo=handle.full_name,
                env=env,
                publish=True,
            )
        elif command.kind == "ask":
            summary = await ask_runner(
                settings,
                number=event.number,
                question=command.question,
                repo=handle.full_name,
                env=env,
            )
            await _publish_ask_reply(
                handle,
                pr_number=event.number,
                summary=summary,
                publisher=issue_comment_publisher,
            )
        elif command.kind == "summary":
            summary = await describe_runner(
                settings,
                number=event.number,
                repo=handle.full_name,
                env=env,
            )
            await _publish_summary_reply(
                handle,
                pr_number=event.number,
                summary=summary,
                publisher=issue_comment_publisher,
            )
        elif command.kind == "configuration":
            await _publish_configuration_reply(
                settings,
                handle,
                pr_number=event.number,
                publisher=issue_comment_publisher,
            )
        elif command.kind == "learn":
            _record_learning(
                settings,
                repo=handle.full_name,
                pr_number=event.number,
                instruction=command.instruction,
                comment=comment,
            )

    command_store.save(state)
    return handled


def _record_learning(
    settings: Settings,
    *,
    repo: str,
    pr_number: int,
    instruction: str,
    comment: Any,
) -> None:
    if not settings.memory.enabled or not settings.memory.learnings_enabled:
        _log.info(
            "start.command_learn_ignored",
            repo=repo,
            pr=pr_number,
            reason="memory_learnings_disabled",
        )
        return
    store = SQLitePullRequestMemory(settings.resolved_memory_path())
    store.add_learning(
        repo=repo,
        instruction=instruction,
        source_pr_number=pr_number,
        source_comment_id=int(getattr(comment, "id", 0) or 0),
        source_url=str(getattr(comment, "html_url", "") or ""),
        author=str(getattr(getattr(comment, "user", None), "login", "") or ""),
        created_at=getattr(comment, "created_at", None),
    )
    _log.info("start.command_learn", repo=repo, pr=pr_number)


async def _publish_ask_reply(
    handle: RepositoryHandle,
    *,
    pr_number: int,
    summary: dict[str, object],
    publisher: IssueCommentPublisher | None,
) -> None:
    body = _format_ask_reply(summary)
    if publisher is not None:
        await publisher(pr_number=pr_number, body=body)
        return
    await handle.create_issue_comment(pr_number, body=body)


async def _publish_summary_reply(
    handle: RepositoryHandle,
    *,
    pr_number: int,
    summary: dict[str, object],
    publisher: IssueCommentPublisher | None,
) -> None:
    await publish_pr_summary_body(
        handle,
        pr_number=pr_number,
        summary=summary,
        publisher=publisher,
    )


async def _publish_configuration_reply(
    settings: Settings,
    handle: RepositoryHandle,
    *,
    pr_number: int,
    publisher: IssueCommentPublisher | None,
) -> None:
    body = _format_configuration_reply(settings, repo=handle.full_name)
    if publisher is not None:
        await publisher(pr_number=pr_number, body=body)
        return
    await handle.create_issue_comment(pr_number, body=body)


def _format_ask_reply(summary: dict[str, object]) -> str:
    answer = summary.get("answer")
    if not isinstance(answer, dict):
        return "## OpenRabbit Answer\n\nI could not produce an answer for this question."
    out = StringIO()
    out.write("## OpenRabbit Answer\n\n")
    text = answer.get("answer")
    out.write(str(text or "I cannot determine that from the provided evidence."))
    evidence = answer.get("evidence")
    if isinstance(evidence, list) and evidence:
        out.write("\n\nEvidence:\n")
        for item in evidence:
            if not isinstance(item, dict):
                continue
            detail = item.get("detail", "")
            file_ = item.get("file", "")
            line = item.get("line")
            location = f" (`{file_}:{line}`)" if file_ and isinstance(line, int) else ""
            out.write(f"- {detail}{location}\n")
    return out.getvalue()


def _format_configuration_reply(settings: Settings, *, repo: str) -> str:
    enabled_reviews = [
        name
        for name in ("security", "performance", "architecture", "bug", "test_coverage", "style")
        if bool(getattr(settings.review, name))
    ]
    base_url = settings.model.base_url or "default"
    memory_state = "enabled" if settings.memory.enabled else "disabled"
    learnings_state = "enabled" if settings.memory.learnings_enabled else "disabled"
    max_changed_files = (
        str(settings.polling.max_changed_files)
        if settings.polling.max_changed_files is not None
        else "unbounded"
    )
    return "\n".join(
        [
            "## OpenRabbit Configuration",
            "",
            f"- Repository: `{repo}`",
            f"- Model provider: `{settings.model.provider}`",
            f"- Model name: `{settings.model.model_name}`",
            f"- Base URL: `{base_url}`",
            f"- Model key env: `{settings.model.api_key_env}`",
            f"- GitHub token env: `{settings.github.token_env}`",
            f"- Review profile: `{settings.review.profile}`",
            f"- Enabled checks: `{', '.join(enabled_reviews) or 'none'}`",
            f"- Memory: `{memory_state}`",
            f"- Learnings: `{learnings_state}`",
            f"- Poll interval: `{settings.polling.interval_seconds}s`",
            f"- Review cooldown: `{settings.polling.review_cooldown_seconds}s`",
            f"- Max concurrent reviews: `{settings.polling.max_concurrent_reviews}`",
            f"- Max changed files: `{max_changed_files}`",
            "",
            "Secrets are not displayed.",
        ]
    )


async def run_start(
    settings: Settings,
    *,
    workspace: Path,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
) -> None:
    """Run the polling service in the foreground until cancelled."""
    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    handle = RepositoryHandle.from_full_name(target, client)
    state_path = workspace / STATE_SUBDIR / STATE_FILENAME
    store = FileStateStore(state_path)
    command_state_path = workspace / STATE_SUBDIR / COMMAND_STATE_FILENAME
    command_store = FileCommandStateStore(command_state_path)

    handler = build_review_handler(
        settings,
        env=env,
        review_runner=review_runner,
        command_store=command_store,
    )

    service = PollingService(
        handle,
        interval_seconds=settings.polling.interval_seconds,
        max_concurrent_handlers=settings.polling.max_concurrent_reviews,
        store=store,
        handler=handler,
    )

    _log.info(
        "start.ready",
        repo=handle.full_name,
        interval_seconds=settings.polling.interval_seconds,
        max_concurrent_reviews=settings.polling.max_concurrent_reviews,
        review_cooldown_seconds=settings.polling.review_cooldown_seconds,
        max_changed_files=settings.polling.max_changed_files,
        state=str(state_path),
        command_state=str(command_state_path),
    )

    try:
        await service.run_forever()
    finally:
        await client.aclose()


def run_start_blocking(
    settings: Settings,
    *,
    workspace: Path,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    review_runner: ReviewRunner | None = None,
) -> None:
    """Synchronous wrapper used by the Typer command."""
    try:
        asyncio.run(
            run_start(
                settings, workspace=workspace, repo=repo, env=env, review_runner=review_runner
            )
        )
    except KeyboardInterrupt:
        _log.info("start.shutdown")
