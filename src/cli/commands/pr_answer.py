"""Stable PR ask-answer comment formatting and publishing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from github_ import RepositoryHandle

ANSWER_MARKER = "<!-- openrabbit:pr-answer -->"


@dataclass(frozen=True)
class PRAnswerPublishResult:
    """Result of creating or updating the stable PR answer comment."""

    action: Literal["created", "updated"]
    comment_id: int
    html_url: str


async def publish_or_update_pr_answer(
    handle: RepositoryHandle,
    *,
    pr_number: int,
    summary: dict[str, object],
) -> PRAnswerPublishResult:
    """Create or update OpenRabbit's single managed PR answer comment."""
    body = format_pr_answer(summary)
    comments = await handle.list_issue_comments(pr_number)
    existing = next(
        (comment for comment in reversed(comments) if ANSWER_MARKER in comment.body), None
    )
    if existing is None:
        comment = await handle.create_issue_comment(pr_number, body=body)
        return PRAnswerPublishResult(
            action="created",
            comment_id=comment.id,
            html_url=comment.html_url,
        )

    comment = await handle.update_issue_comment(existing.id, body=body)
    return PRAnswerPublishResult(
        action="updated",
        comment_id=comment.id,
        html_url=comment.html_url,
    )


def format_pr_answer(summary: dict[str, object]) -> str:
    """Render a managed PR answer comment from an ask summary."""
    answer = summary.get("answer")
    answer_data = answer if isinstance(answer, dict) else {}
    lines = [
        ANSWER_MARKER,
        "## OpenRabbit Answer",
        "",
        _metadata_line(summary),
        "",
        "### Question",
        str(summary.get("question") or "No question provided."),
        "",
        "### Answer",
        str(answer_data.get("answer") or "No answer generated."),
    ]
    _append_evidence(lines, answer_data.get("evidence"))
    _append_list(lines, "Uncertainty", answer_data.get("uncertainty"))
    _append_list(lines, "Follow-up Checks", answer_data.get("follow_up_checks"))
    _append_context_sources(lines, summary.get("context_provenance"))
    return "\n".join(lines).rstrip() + "\n"


def _metadata_line(summary: dict[str, object]) -> str:
    state = str(summary.get("state") or "unknown")
    head = str(summary.get("head_sha") or "unknown")
    files = summary.get("files_changed", 0)
    context = "loaded" if summary.get("context_loaded") is True else "diff only"
    return (
        f"**Status:** {state} | **Head:** `{head}` | **Files:** {files} | "
        f"**Context:** {context}"
    )


def _append_evidence(lines: list[str], value: object) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.extend(["", "### Evidence"])
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        detail = str(item.get("detail") or "").strip()
        file_ = str(item.get("file") or "").strip()
        line = item.get("line")
        if not source or not detail:
            continue
        location = file_
        if location and isinstance(line, int):
            location = f"{location}:{line}"
        prefix = f"`{source}`"
        if location:
            prefix = f"{prefix} `{location}`"
        lines.append(f"- {prefix}: {detail}")


def _append_list(lines: list[str], title: str, value: object) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    lines.extend(["", f"### {title}"])
    for item in items[:8]:
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
