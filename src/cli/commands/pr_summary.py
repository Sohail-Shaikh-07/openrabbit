"""Stable PR summary comment formatting and publishing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from github_ import RepositoryHandle

SUMMARY_MARKER = "<!-- openrabbit:pr-summary -->"


@dataclass(frozen=True)
class PRSummaryPublishResult:
    """Result of creating or updating the stable PR summary comment."""

    action: Literal["created", "updated"]
    comment_id: int
    html_url: str


IssueCommentPublisher = Callable[..., Awaitable[None]]


async def publish_or_update_pr_summary(
    handle: RepositoryHandle,
    *,
    pr_number: int,
    summary: dict[str, object],
) -> PRSummaryPublishResult:
    """Create or update OpenRabbit's single managed PR summary comment."""
    body = format_pr_walkthrough_summary(summary)
    comments = await handle.list_issue_comments(pr_number)
    existing = next(
        (comment for comment in reversed(comments) if SUMMARY_MARKER in comment.body), None
    )
    if existing is None:
        comment = await handle.create_issue_comment(pr_number, body=body)
        return PRSummaryPublishResult(
            action="created",
            comment_id=comment.id,
            html_url=comment.html_url,
        )

    comment = await handle.update_issue_comment(existing.id, body=body)
    return PRSummaryPublishResult(
        action="updated",
        comment_id=comment.id,
        html_url=comment.html_url,
    )


async def publish_pr_summary_body(
    handle: RepositoryHandle,
    *,
    pr_number: int,
    summary: dict[str, object],
    publisher: IssueCommentPublisher | None = None,
) -> PRSummaryPublishResult | None:
    """Publish a formatted PR summary through tests or the real GitHub updater."""
    body = format_pr_walkthrough_summary(summary)
    if publisher is not None:
        await publisher(pr_number=pr_number, body=body)
        return None
    return await publish_or_update_pr_summary(handle, pr_number=pr_number, summary=summary)


def format_pr_walkthrough_summary(summary: dict[str, object]) -> str:
    """Render a CodeRabbit-style PR overview comment from a describe summary."""
    description = summary.get("description")
    desc = description if isinstance(description, dict) else {}
    lines = [
        SUMMARY_MARKER,
        "## OpenRabbit PR Summary",
        "",
        _metadata_line(summary),
        "",
        str(desc.get("summary") or "No summary generated."),
    ]

    _append_walkthrough(lines, desc.get("walkthrough"))
    _append_list(lines, "Risky Files", desc.get("risk_areas"))
    _append_list(lines, "Testing Focus", desc.get("testing_focus"))
    _append_context_sources(lines, summary.get("context_provenance"))
    _append_follow_up_commands(lines)
    return "\n".join(lines).rstrip() + "\n"


def _metadata_line(summary: dict[str, object]) -> str:
    state = str(summary.get("state") or "unknown")
    head = str(summary.get("head_sha") or "unknown")
    files = summary.get("files_changed", 0)
    context = "loaded" if summary.get("context_loaded") is True else "diff only"
    review_status = str(summary.get("review_status") or "summary generated")
    return (
        f"**Status:** {state} | **Head:** `{head}` | **Files:** {files} | "
        f"**Context:** {context} | **Review:** {review_status}"
    )


def _append_walkthrough(lines: list[str], value: object) -> None:
    if not isinstance(value, list) or not value:
        return
    lines.extend(["", "### Walkthrough"])
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        file_ = str(item.get("file") or "").strip()
        notes = str(item.get("notes") or "").strip()
        if file_ and notes:
            lines.append(f"- `{file_}`: {notes}")


def _append_list(lines: list[str], title: str, value: object) -> None:
    if not isinstance(value, list) or not value:
        return
    lines.extend(["", f"### {title}"])
    for item in value[:8]:
        text = str(item).strip()
        if text:
            lines.append(f"- {text}")


def _append_context_sources(lines: list[str], value: object) -> None:
    sources = value if isinstance(value, list) else []
    lines.extend(["", "### Context Sources"])
    if not sources:
        lines.append("- Diff only")
        return
    for item in sources[:8]:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("source_path") or "").strip()
        dimension = str(item.get("dimension") or "").strip()
        reason = str(item.get("retrieval_reason") or "").strip()
        if not source_path:
            continue
        suffix = " ".join(part for part in (dimension, reason) if part)
        detail = f" ({suffix})" if suffix else ""
        lines.append(f"- `{source_path}`{detail}")


def _append_follow_up_commands(lines: list[str]) -> None:
    lines.extend(
        [
            "",
            "### Follow-up Commands",
            "- `/openrabbit review`",
            "- `/openrabbit full review`",
            "- `/openrabbit improve`",
            "- `/openrabbit ask <question>`",
            "- `/openrabbit pause` / `/openrabbit resume`",
        ]
    )
