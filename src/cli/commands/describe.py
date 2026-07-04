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
    format_changed_line_evidence,
    format_prompt_diff,
)
from cli.commands.review import ContextLoader, _has_retrieval_context, _load_review_context
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import GitHubClient, PullRequestParser, RepositoryHandle

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
) -> dict[str, object]:
    """Fetch a PR, generate a read-only description, and return a summary dict."""
    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    try:
        handle = RepositoryHandle.from_full_name(target, client)
        payload = await PullRequestParser(handle).parse(number)
    finally:
        await client.aclose()

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

    describe = generator or _generate_description
    description = await describe(
        payload,
        settings=settings,
        retrieval_result=retrieval_result,
        env=env,
    )

    hunk_total = sum(len(f.hunks) for f in payload.files)
    binary_count = sum(1 for f in payload.files if f.is_binary)
    return {
        "repo": handle.full_name,
        "number": payload.number,
        "title": payload.pull_request.title,
        "state": payload.pull_request.state,
        "head_sha": payload.head_sha[:12],
        "files_changed": len(payload.files),
        "binary_files": binary_count,
        "hunks": hunk_total,
        "commits": len(payload.commits),
        "context_loaded": _has_retrieval_context(retrieval_result),
        "description": _serialize_description(description),
    }


def run_describe_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(run_describe(settings, number=number, repo=repo, env=env))


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


async def _generate_description(
    pr_payload: Any,
    *,
    settings: Settings,
    retrieval_result: Any | None,
    env: dict[str, str] | None,
) -> PullRequestDescription:
    client = _build_description_client(settings, env=env)
    prompt = _build_prompt(pr_payload, retrieval_result)
    raw = await client.generate(prompt)
    return _parse_description(raw)


def _build_description_client(settings: Settings, *, env: dict[str, str] | None) -> LLMClient:
    api_key = (
        settings.resolved_model_api_key(env=env)
        if settings.model.provider in {"openai", "openai-compatible"}
        else None
    )
    return build_llm_client(settings.model, api_key=api_key)


def _build_prompt(pr_payload: Any, retrieval_result: Any | None) -> str:
    state: ReviewState = {"pr_payload": pr_payload, "retrieval_result": retrieval_result}
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
