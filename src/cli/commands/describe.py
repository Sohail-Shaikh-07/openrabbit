"""Implementation of ``openrabbit describe --pr N``.

The describe command is read-only: it fetches a pull request, optionally loads
repository context, asks the configured model provider for a concise summary,
and prints the result locally.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TextIO

from agents.factory import build_llm_client
from agents.llm import LLMClient
from agents.models import ReviewState
from agents.prompting import (
    NO_PROJECT_CONTEXT,
    REVIEW_DISCIPLINE,
    collect_context,
    collect_history_context,
    format_changed_line_evidence,
    format_prompt_diff,
)
from cli.commands.history import load_pr_history
from cli.commands.output import render_json
from cli.commands.pr_summary import (
    PRSummaryPublishResult,
    publish_or_update_pr_summary,
)
from cli.commands.review import (
    ContextLoader,
    _context_provenance,
    _has_retrieval_context,
    _load_review_context,
)
from cli.commands.review_context import filter_model_review_context
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import GitHubClient, PullRequestParser, RepositoryHandle
from memory.history import PullRequestHistory
from review_controls import prepare_review_controls

_log = get_logger(__name__)


@dataclass(frozen=True)
class PullRequestDescription:
    """Structured model output for a PR walkthrough."""

    summary: str
    changed_files: list[str] = field(default_factory=list)
    risk_areas: list[str] = field(default_factory=list)
    testing_focus: list[str] = field(default_factory=list)
    walkthrough: list[dict[str, str]] = field(default_factory=list)


DescriptionGenerator = Callable[..., Awaitable[PullRequestDescription]]
SummaryPublisher = Callable[..., Awaitable[PRSummaryPublishResult]]

_PROMPT_TEMPLATE = """You are OpenRabbit's PR describe agent. Explain this pull request like a senior maintainer preparing another reviewer to inspect it quickly.

Mission:
- Summarize what changed, why it matters, and where a reviewer should focus.
- Stay grounded in the changed-line evidence, diff, metadata, and project context provided below.
- Do not invent files, product requirements, test results, tickets, or runtime behavior.
- Keep the output concise, practical, and useful before a code review.
- Flag risk areas as review focus, not as confirmed bugs.

Pull request:
- Number: {number}
- Title: {title}
- State: {state}
- Base: {base_ref}
- Head: {head_ref}
- Commits: {commits}
- Files changed: {files_changed}
- Binary files: {binary_files}
- Hunks: {hunks}

Project context:
{project_context}

PR history context:
{history_context}

{changed_line_evidence}

Diff:
{diff}

{review_discipline}

Reply with ONLY a JSON object in this exact format, no prose:
{{
  "summary": "One concise paragraph explaining the PR.",
  "changed_files": ["Short bullet about an important changed file or area."],
  "risk_areas": ["Review focus or risk area grounded in the diff."],
  "testing_focus": ["Specific behavior or path reviewers should verify."],
  "walkthrough": [
    {{"file": "path/to/file.py", "notes": "What changed in this file and why it matters."}}
  ]
}}
"""


async def run_describe(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    generator: DescriptionGenerator | None = None,
    context_loader: ContextLoader | None = None,
    publish: bool = False,
    summary_publisher: SummaryPublisher | None = None,
) -> dict[str, object]:
    """Fetch a PR, generate a read-only description, and return a summary dict."""
    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    try:
        handle = RepositoryHandle.from_full_name(target, client)
        payload = await PullRequestParser(handle).parse(number)
        original_payload = payload
        controls_result = await prepare_review_controls(
            payload,
            settings.review,
            source_loader=handle.get_file_text,
        )
        payload = controls_result.filtered_payload
        pr_history_result = await load_pr_history(settings, handle=handle, payload=payload)

        retrieval_result: Any | None = None
        loader = context_loader or _load_review_context
        try:
            retrieval_result = await loader(payload)
        except Exception as exc:
            _log.warning(
                "describe.context_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            retrieval_result = None

        model_context = filter_model_review_context(
            controls_result,
            retrieval_result=retrieval_result,
            pr_history=pr_history_result.history,
        )
        retrieval_result = model_context.retrieval_result

        describe = generator or _generate_description
        description = await describe(
            payload,
            settings=settings,
            retrieval_result=retrieval_result,
            pr_history=model_context.pr_history,
            env=env,
        )

        hunk_total = sum(len(f.hunks) for f in original_payload.files)
        binary_count = sum(1 for f in original_payload.files if f.is_binary)
        summary: dict[str, object] = {
            "repo": handle.full_name,
            "number": payload.number,
            "title": payload.pull_request.title,
            "state": payload.pull_request.state,
            "head_sha": payload.head_sha[:12],
            "files_changed": len(original_payload.files),
            "binary_files": binary_count,
            "hunks": hunk_total,
            "commits": len(original_payload.commits),
            "ast_instruction_count": len(controls_result.ast_matches),
            "review_control_warning_count": len(controls_result.warnings),
            "review_control_warnings": [item.as_dict() for item in controls_result.warnings],
            "ast_unsupported_path_count": len(controls_result.unsupported_paths),
            "context_loaded": _has_retrieval_context(retrieval_result),
            "context_provenance": _context_provenance(retrieval_result),
            "conversation_count": pr_history_result.conversation_count,
            "learning_count": pr_history_result.learning_count,
            "review_status": "summary generated",
            "publish_status": "read_only",
            "description": _serialize_description(description),
        }
        if publish:
            publisher = summary_publisher or publish_or_update_pr_summary
            result = await publisher(handle, pr_number=payload.number, summary=summary)
            summary["publish_status"] = result.action
            summary["summary_comment_id"] = result.comment_id
            summary["summary_comment_url"] = result.html_url
        return summary
    finally:
        await client.aclose()


def run_describe_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    publish: bool = False,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(run_describe(settings, number=number, repo=repo, env=env, publish=publish))


def render_description(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_describe`."""
    print(f"PR #{summary['number']} on {summary['repo']}", file=out)
    print(f"  Title:        {summary['title']}", file=out)
    print(f"  State:        {summary['state']}", file=out)
    print(f"  Head SHA:     {summary['head_sha']}", file=out)
    print(
        f"  Files:        {summary['files_changed']} ({summary['binary_files']} binary)",
        file=out,
    )
    print(f"  Hunks:        {summary['hunks']}", file=out)
    print(f"  Commits:      {summary['commits']}", file=out)
    context_loaded = summary.get("context_loaded")
    if isinstance(context_loaded, bool):
        print(f"  Context:      {'loaded' if context_loaded else 'diff only'}", file=out)
    _print_publish_status(summary, out, markdown=False)

    description = summary.get("description")
    if not isinstance(description, dict):
        return

    print("", file=out)
    print("Summary:", file=out)
    print(f"  {description.get('summary', '')}", file=out)
    _print_list("Changed files:", description.get("changed_files"), out)
    _print_list("Risk areas:", description.get("risk_areas"), out)
    _print_list("Testing focus:", description.get("testing_focus"), out)
    _print_walkthrough(description.get("walkthrough"), out)


def render_description_markdown(summary: dict[str, object], out: TextIO) -> None:
    """Render a PR description as Markdown."""
    print(f"# PR #{summary['number']}: {summary['title']}", file=out)
    print("", file=out)
    _print_metadata(summary, out, markdown=True)
    _print_publish_status(summary, out, markdown=True)

    description = summary.get("description")
    if not isinstance(description, dict):
        return

    print("", file=out)
    print("## Summary", file=out)
    print("", file=out)
    print(str(description.get("summary", "")), file=out)
    _print_markdown_list("Changed Files", description.get("changed_files"), out)
    _print_markdown_list("Risk Areas", description.get("risk_areas"), out)
    _print_markdown_list("Testing Focus", description.get("testing_focus"), out)
    _print_markdown_walkthrough(description.get("walkthrough"), out)


def render_description_json(summary: dict[str, object], out: TextIO) -> None:
    """Render a PR description as deterministic JSON."""
    render_json(summary, out)


async def _generate_description(
    pr_payload: Any,
    *,
    settings: Settings,
    retrieval_result: Any | None,
    pr_history: PullRequestHistory | None = None,
    env: dict[str, str] | None,
) -> PullRequestDescription:
    client = _build_description_client(settings, env=env)
    prompt = _build_prompt(pr_payload, retrieval_result, pr_history)
    raw = await client.generate(prompt)
    return _parse_description(raw)


def _build_description_client(settings: Settings, *, env: dict[str, str] | None) -> LLMClient:
    api_key = (
        settings.resolved_model_api_key(env=env) if settings.model.provider != "ollama" else None
    )
    return build_llm_client(settings.model, api_key=api_key)


def _build_prompt(
    pr_payload: Any,
    retrieval_result: Any | None,
    pr_history: PullRequestHistory | None = None,
) -> str:
    state: ReviewState = {
        "pr_payload": pr_payload,
        "retrieval_result": retrieval_result,
        "pr_history": pr_history,
    }
    pr = pr_payload.pull_request
    hunk_total = sum(len(f.hunks) for f in pr_payload.files)
    binary_count = sum(1 for f in pr_payload.files if f.is_binary)
    project_context = collect_context(state, "security", "architecture", "performance", "tests")
    if project_context == NO_PROJECT_CONTEXT:
        project_context = "(No project context retrieved. Describe the PR from the diff only.)"

    return _PROMPT_TEMPLATE.format(
        number=pr_payload.number,
        title=pr.title,
        state=pr.state,
        base_ref=pr.base.ref,
        head_ref=pr.head.ref,
        commits=len(pr_payload.commits),
        files_changed=len(pr_payload.files),
        binary_files=binary_count,
        hunks=hunk_total,
        project_context=project_context,
        history_context=collect_history_context(state),
        changed_line_evidence=format_changed_line_evidence(pr_payload),
        diff=format_prompt_diff(pr_payload),
        review_discipline=REVIEW_DISCIPLINE,
    )


def _parse_description(raw: str) -> PullRequestDescription:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return PullRequestDescription(summary=raw.strip() or "No summary generated.")
    if not isinstance(data, dict):
        return PullRequestDescription(summary="No summary generated.")

    return PullRequestDescription(
        summary=_clean_text(data.get("summary")) or "No summary generated.",
        changed_files=_string_list(data.get("changed_files")),
        risk_areas=_string_list(data.get("risk_areas")),
        testing_focus=_string_list(data.get("testing_focus")),
        walkthrough=_walkthrough_list(data.get("walkthrough")),
    )


def _serialize_description(description: PullRequestDescription) -> dict[str, object]:
    return {
        "summary": description.summary,
        "changed_files": description.changed_files,
        "risk_areas": description.risk_areas,
        "testing_focus": description.testing_focus,
        "walkthrough": description.walkthrough,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            cleaned.append(text)
    return cleaned


def _walkthrough_list(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    walkthrough: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        file_ = _clean_text(item.get("file"))
        notes = _clean_text(item.get("notes"))
        if file_ and notes:
            walkthrough.append({"file": file_, "notes": notes})
    return walkthrough


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _print_list(title: str, value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print(title, file=out)
    for item in items:
        print(f"  - {item}", file=out)


def _print_walkthrough(value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print("Walkthrough:", file=out)
    for item in items:
        if not isinstance(item, dict):
            continue
        print(f"  - {item.get('file', '')}: {item.get('notes', '')}", file=out)


def _print_metadata(summary: dict[str, object], out: TextIO, *, markdown: bool) -> None:
    context_loaded = summary.get("context_loaded")
    context = (
        "loaded"
        if isinstance(context_loaded, bool) and context_loaded
        else "diff only" if isinstance(context_loaded, bool) else "unknown"
    )
    rows = [
        ("Repository", summary.get("repo", "")),
        ("State", summary.get("state", "")),
        ("Head SHA", summary.get("head_sha", "")),
        ("Files", f"{summary.get('files_changed', 0)} ({summary.get('binary_files', 0)} binary)"),
        ("Hunks", summary.get("hunks", "")),
        ("Commits", summary.get("commits", "")),
        ("Context", context),
    ]
    for label, value in rows:
        prefix = "-" if markdown else " "
        print(f"{prefix} {label}: {value}", file=out)


def _print_publish_status(summary: dict[str, object], out: TextIO, *, markdown: bool) -> None:
    publish_status = summary.get("publish_status")
    if publish_status not in {"created", "updated"}:
        return
    url = str(summary.get("summary_comment_url") or "").strip()
    label = "created" if publish_status == "created" else "updated"
    value = f"summary comment {label}"
    if url:
        value = f"{value} ({url})"
    if markdown:
        print(f"- Published: {value}", file=out)
    else:
        print(f"  Published:    {value}", file=out)


def _print_markdown_list(title: str, value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print(f"## {title}", file=out)
    print("", file=out)
    for item in items:
        print(f"- {item}", file=out)


def _print_markdown_walkthrough(value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print("## Walkthrough", file=out)
    print("", file=out)
    for item in items:
        if not isinstance(item, dict):
            continue
        print(f"- `{item.get('file', '')}`: {item.get('notes', '')}", file=out)
