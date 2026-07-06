"""Implementation of ``openrabbit improve --pr N``.

The improve command is read-only. It asks the configured model provider for
small fix suggestions and then grounds those suggestions to changed PR lines
before anything is printed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from ranking.grounding import DiffGroundingIndex, build_diff_grounding_index

_log = get_logger(__name__)


@dataclass(frozen=True)
class ImprovementSuggestion:
    """One grounded improvement suggestion for a changed line."""

    file: str
    line: int
    title: str
    reason: str
    suggestion: str
    fix: str = ""


@dataclass(frozen=True)
class ImprovementResult:
    """Model suggestions after grounding."""

    suggestions: list[ImprovementSuggestion]
    dropped_suggestions_count: int = 0


ImprovementGenerator = Callable[..., Awaitable[list[ImprovementSuggestion]]]

_PROMPT_TEMPLATE = """You are OpenRabbit's improve agent. Propose small, reviewable fixes for this pull request.

Mission:
- Suggest only concrete improvements that are grounded in changed lines.
- Prefer one small fix over broad rewrites, style preferences, or speculative architecture changes.
- Point each suggestion at a changed new-side line from the changed-line evidence.
- Keep fixes minimal and compatible with the surrounding code style.
- Do not suggest changes for unchanged files, generated files, binary files, or behavior not visible in the provided context.
- Return no suggestions when the diff does not justify a small actionable fix.

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
  "suggestions": [
    {{
      "file": "path/to/file.py",
      "line": 42,
      "title": "Short title",
      "reason": "Why this changed line should be improved.",
      "suggestion": "Specific small change to make.",
      "fix": "Optional minimal replacement snippet or patch"
    }}
  ]
}}

If there are no grounded small fixes, return {{"suggestions": []}}.
"""


async def run_improve(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    generator: ImprovementGenerator | None = None,
    context_loader: ContextLoader | None = None,
) -> dict[str, object]:
    """Fetch a PR, generate grounded improvements, and return a summary dict."""
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
            "improve.context_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        retrieval_result = None

    improve = generator or _generate_improvements
    raw_suggestions = await improve(
        payload,
        settings=settings,
        retrieval_result=retrieval_result,
        env=env,
    )
    result = _ground_suggestions(raw_suggestions, payload)

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
        "suggestions_count": len(result.suggestions),
        "dropped_suggestions_count": result.dropped_suggestions_count,
        "suggestions": [_serialize_suggestion(suggestion) for suggestion in result.suggestions],
    }


def run_improve_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(run_improve(settings, number=number, repo=repo, env=env))


def render_improvements(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_improve`."""
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
    print(f"  Suggestions:  {summary['suggestions_count']}", file=out)
    dropped = summary.get("dropped_suggestions_count")
    if isinstance(dropped, int) and dropped > 0:
        print(f"  Dropped:      {dropped} ungrounded", file=out)
    context_loaded = summary.get("context_loaded")
    if isinstance(context_loaded, bool):
        print(f"  Context:      {'loaded' if context_loaded else 'diff only'}", file=out)

    raw_suggestions = summary.get("suggestions")
    suggestions = raw_suggestions if isinstance(raw_suggestions, list) else []
    if not suggestions:
        return

    print("", file=out)
    print("Improvement suggestions:", file=out)
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        location = f"{item.get('file', '')}:{item.get('line', '')}"
        print(f"  - {item.get('title', '')} ({location})", file=out)
        print(f"    {item.get('reason', '')}", file=out)
        print(f"    Suggestion: {item.get('suggestion', '')}", file=out)
        fix = item.get("fix")
        if isinstance(fix, str) and fix:
            print("    Fix:", file=out)
            print(_indent_block(fix), file=out)


async def _generate_improvements(
    pr_payload: Any,
    *,
    settings: Settings,
    retrieval_result: Any | None,
    env: dict[str, str] | None,
) -> list[ImprovementSuggestion]:
    client = _build_improve_client(settings, env=env)
    prompt = _build_prompt(pr_payload, retrieval_result)
    raw = await client.generate(prompt)
    return _parse_suggestions(raw)


def _build_improve_client(settings: Settings, *, env: dict[str, str] | None) -> LLMClient:
    api_key = (
        settings.resolved_model_api_key(env=env) if settings.model.provider != "ollama" else None
    )
    return build_llm_client(settings.model, api_key=api_key)


def _build_prompt(pr_payload: Any, retrieval_result: Any | None) -> str:
    state: ReviewState = {"pr_payload": pr_payload, "retrieval_result": retrieval_result}
    pr = pr_payload.pull_request
    hunk_total = sum(len(f.hunks) for f in pr_payload.files)
    binary_count = sum(1 for f in pr_payload.files if f.is_binary)
    project_context = collect_context(
        state, "bug", "security", "architecture", "performance", "tests"
    )
    if project_context == NO_PROJECT_CONTEXT:
        project_context = "(No project context retrieved. Suggest improvements from the diff only.)"

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


def _parse_suggestions(raw: str) -> list[ImprovementSuggestion]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []

    raw_suggestions = data.get("suggestions")
    if not isinstance(raw_suggestions, list):
        return []

    suggestions: list[ImprovementSuggestion] = []
    for item in raw_suggestions:
        if not isinstance(item, dict):
            continue
        file_ = _normalise_path(_clean_text(item.get("file")))
        line = _line_number(item.get("line"))
        title = _clean_text(item.get("title"))
        reason = _clean_text(item.get("reason"))
        suggestion = _clean_text(item.get("suggestion"))
        fix = _clean_fix(item.get("fix"))
        if file_ and line > 0 and title and reason and suggestion:
            suggestions.append(
                ImprovementSuggestion(
                    file=file_,
                    line=line,
                    title=title,
                    reason=reason,
                    suggestion=suggestion,
                    fix=fix,
                )
            )
    return suggestions


def _ground_suggestions(
    suggestions: list[ImprovementSuggestion],
    pr_payload: Any,
) -> ImprovementResult:
    index = build_diff_grounding_index(pr_payload)
    if not index.changed_files:
        return ImprovementResult(suggestions=suggestions)

    kept: list[ImprovementSuggestion] = []
    dropped = 0
    for suggestion in suggestions:
        if _is_grounded(suggestion, index):
            kept.append(suggestion)
        else:
            dropped += 1
    return ImprovementResult(suggestions=kept, dropped_suggestions_count=dropped)


def _is_grounded(suggestion: ImprovementSuggestion, index: DiffGroundingIndex) -> bool:
    file_ = _normalise_path(suggestion.file)
    if file_ not in index.changed_files:
        return False
    return suggestion.line in index.changed_lines.get(file_, frozenset())


def _serialize_suggestion(suggestion: ImprovementSuggestion) -> dict[str, object]:
    return {
        "file": suggestion.file,
        "line": suggestion.line,
        "title": suggestion.title,
        "reason": suggestion.reason,
        "suggestion": suggestion.suggestion,
        "fix": suggestion.fix,
    }


def _line_number(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _clean_fix(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalise_path(path: str) -> str:
    clean = path.strip().replace("\\", "/")
    if clean.startswith(("a/", "b/")):
        clean = clean[2:]
    return clean.lstrip("/")


def _indent_block(text: str) -> str:
    return "\n".join(f"      {line}" for line in text.splitlines())
