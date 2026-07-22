"""Implementation of ``openrabbit improve --pr N``.

The improve command is read-only by default. It asks the configured model
provider for small fix suggestions, grounds those suggestions to changed PR
lines, and only publishes them to GitHub when explicitly requested.
"""

from __future__ import annotations

import asyncio
import json
import re
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
    collect_history_context,
    format_changed_line_evidence,
    format_prompt_diff,
)
from cli.commands.history import load_pr_history
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
from github_ import (
    GitHubAuthError,
    GitHubClient,
    PullRequestParser,
    RepositoryHandle,
    ReviewComment,
)
from knowledge.context import load_connector_context
from knowledge.diagnostics import build_context_precision_diagnostics
from memory.history import PullRequestHistory
from ranking.grounding import DiffGroundingIndex, build_diff_grounding_index
from review_controls import prepare_review_controls

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
ImprovementPublisher = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class ImprovementPublishPlan:
    """Suggestions separated by the safest GitHub publishing target."""

    inline_suggestions: list[ReviewComment]
    inline_source_suggestions: list[ImprovementSuggestion]
    summary_suggestions: list[ImprovementSuggestion]
    dropped_actionability_count: int = 0


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

PR history context:
{history_context}

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
    dry_run: bool = False,
    publish: bool = False,
    publisher: ImprovementPublisher | None = None,
) -> dict[str, object]:
    """Fetch a PR, generate grounded improvements, and return a summary dict."""
    if dry_run and publish:
        raise ValueError("--dry-run and --publish cannot be used together")

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

    connector_context = load_connector_context(
        settings,
        payload,
        repo=handle.full_name,
        env=env,
        retrieval_result=retrieval_result,
    )
    retrieval_result = connector_context.retrieval_result
    model_context = filter_model_review_context(
        controls_result,
        retrieval_result=retrieval_result,
        pr_history=pr_history_result.history,
    )
    retrieval_result = model_context.retrieval_result

    improve = generator or _generate_improvements
    raw_suggestions = await improve(
        payload,
        settings=settings,
        retrieval_result=retrieval_result,
        pr_history=model_context.pr_history,
        env=env,
    )
    result = _ground_suggestions(raw_suggestions, payload)
    publish_plan = _build_publish_plan(result.suggestions, payload)

    publish_status = "dry_run"
    published_inline_count = 0
    published_summary_count = 0
    if publish:
        if publish_plan.inline_suggestions or publish_plan.summary_suggestions:
            await _publish_improvements(
                settings,
                env=env,
                handle=handle,
                pr_number=payload.number,
                head_sha=payload.head_sha,
                plan=publish_plan,
                publisher=publisher,
            )
            publish_status = "posted"
            published_inline_count = len(publish_plan.inline_suggestions)
            published_summary_count = len(publish_plan.summary_suggestions)
        else:
            publish_status = "no_suggestions"

    hunk_total = sum(len(f.hunks) for f in original_payload.files)
    binary_count = sum(1 for f in original_payload.files if f.is_binary)
    return {
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
        "context_diagnostics": build_context_precision_diagnostics(
            retrieval_result,
            connector_context=connector_context.summary,
            command="improve",
        ),
        "connector_context": connector_context.summary,
        "conversation_count": pr_history_result.conversation_count,
        "learning_count": pr_history_result.learning_count,
        "suggestions_count": len(publish_plan.inline_suggestions)
        + len(publish_plan.summary_suggestions),
        "dropped_suggestions_count": result.dropped_suggestions_count,
        "dropped_actionability_count": publish_plan.dropped_actionability_count,
        "publish_status": publish_status,
        "published_inline_count": published_inline_count,
        "published_summary_count": published_summary_count,
        "suggestions": [
            _serialize_suggestion(suggestion)
            for suggestion in _publishable_suggestions(publish_plan)
        ],
    }


def run_improve_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
    publish: bool = False,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(
        run_improve(
            settings,
            number=number,
            repo=repo,
            env=env,
            dry_run=dry_run,
            publish=publish,
        )
    )


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
    dropped_actionability = summary.get("dropped_actionability_count")
    if isinstance(dropped_actionability, int) and dropped_actionability > 0:
        print(f"  Dropped:      {dropped_actionability} non-actionable", file=out)
    context_loaded = summary.get("context_loaded")
    if isinstance(context_loaded, bool):
        print(f"  Context:      {'loaded' if context_loaded else 'diff only'}", file=out)
    publish_status = summary.get("publish_status")
    if publish_status == "posted":
        inline = summary.get("published_inline_count", 0)
        summary_count = summary.get("published_summary_count", 0)
        print(f"  Published:    yes ({inline} inline, {summary_count} summary)", file=out)
    elif publish_status == "no_suggestions":
        print("  Published:    no suggestions to post", file=out)
    elif publish_status == "dry_run":
        print("  Published:    no (dry run)", file=out)

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
    pr_history: PullRequestHistory | None = None,
    env: dict[str, str] | None,
) -> list[ImprovementSuggestion]:
    client = _build_improve_client(settings, env=env)
    prompt = _build_prompt(pr_payload, retrieval_result, pr_history)
    raw = await client.generate(prompt)
    return _parse_suggestions(raw)


def _build_improve_client(settings: Settings, *, env: dict[str, str] | None) -> LLMClient:
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
        history_context=collect_history_context(state),
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


def _build_publish_plan(
    suggestions: list[ImprovementSuggestion],
    pr_payload: Any | None = None,
) -> ImprovementPublishPlan:
    inline: list[ReviewComment] = []
    inline_source: list[ImprovementSuggestion] = []
    summary: list[ImprovementSuggestion] = []
    dropped = 0
    source_by_path = _source_text_by_path(pr_payload)
    for suggestion in suggestions:
        source_text = source_by_path.get(_normalise_path(suggestion.file))
        if not _is_actionable(suggestion, source_text=source_text):
            dropped += 1
            continue
        if _has_safe_replacement(suggestion):
            inline.append(_to_review_comment(suggestion))
            inline_source.append(suggestion)
        else:
            summary.append(suggestion)
    return ImprovementPublishPlan(
        inline_suggestions=inline,
        inline_source_suggestions=inline_source,
        summary_suggestions=summary,
        dropped_actionability_count=dropped,
    )


def _publishable_suggestions(plan: ImprovementPublishPlan) -> list[ImprovementSuggestion]:
    return plan.inline_source_suggestions + plan.summary_suggestions


async def _publish_improvements(
    settings: Settings,
    *,
    env: dict[str, str] | None,
    handle: RepositoryHandle,
    pr_number: int,
    head_sha: str,
    plan: ImprovementPublishPlan,
    publisher: ImprovementPublisher | None,
) -> None:
    if publisher is not None:
        await publisher(
            pr_number=pr_number,
            inline_suggestions=plan.inline_suggestions,
            summary_suggestions=plan.summary_suggestions,
            head_sha=head_sha,
        )
        return

    token = settings.resolved_github_token(env=env)
    if not token:
        raise GitHubAuthError("cannot publish improvements without a resolved GitHub token")

    async with GitHubClient(token=token) as client:
        await client.create_review(
            handle.owner,
            handle.repo,
            pr_number,
            body=_build_publish_body(plan),
            event="COMMENT",
            comments=plan.inline_suggestions,
            commit_id=head_sha,
        )


def _to_review_comment(suggestion: ImprovementSuggestion) -> ReviewComment:
    body = (
        f"**{suggestion.title}**\n\n"
        f"{suggestion.reason}\n\n"
        f"```suggestion\n{suggestion.fix.strip()}\n```\n\n"
        f"{suggestion.suggestion}"
    )
    return ReviewComment(path=suggestion.file, body=body, line=suggestion.line, side="RIGHT")


def _build_publish_body(plan: ImprovementPublishPlan) -> str:
    lines = ["## OpenRabbit Improvements\n\n"]
    inline_count = len(plan.inline_suggestions)
    summary_count = len(plan.summary_suggestions)
    if inline_count:
        lines.append(
            f"Posted **{inline_count}** inline suggestion"
            f"{'s' if inline_count != 1 else ''} on changed lines.\n\n"
        )
    if summary_count:
        lines.append("Broader suggestions:\n\n")
        for suggestion in plan.summary_suggestions:
            lines.append(
                f"- **{suggestion.title}** (`{suggestion.file}:{suggestion.line}`): "
                f"{suggestion.suggestion}\n"
            )
    if not inline_count and not summary_count:
        lines.append("No actionable improvement suggestions found.\n")
    return "".join(lines)


def _is_actionable(
    suggestion: ImprovementSuggestion,
    *,
    source_text: str | None = None,
) -> bool:
    text = " ".join(
        (suggestion.title, suggestion.reason, suggestion.suggestion, suggestion.fix)
    ).lower()
    if "todo" in text or "fixme" in text:
        return False
    if "add a comment" in text or "comment-only" in text:
        return False
    if not suggestion.fix and _starts_vague(suggestion.suggestion):
        return False
    if "refactor" in suggestion.suggestion.lower() and not suggestion.fix:
        return False
    if not suggestion.fix:
        return True
    if _is_comment_only_fix(suggestion.fix) or _has_placeholder_fix_comment(suggestion.fix):
        return False
    return not _introduces_unavailable_security_dependency(
        suggestion.fix,
        source_text=source_text,
    )


def _has_safe_replacement(suggestion: ImprovementSuggestion) -> bool:
    return bool(suggestion.fix.strip()) and not _is_comment_only_fix(suggestion.fix)


def _starts_vague(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.startswith(("consider ", "maybe ", "think about ", "you could "))


def _is_comment_only_fix(fix: str) -> bool:
    lines = [line.strip() for line in fix.splitlines() if line.strip()]
    if not lines:
        return False
    prefixes = ("#", "//", "/*", "*", "<!--")
    return all(line.startswith(prefixes) for line in lines)


def _has_placeholder_fix_comment(fix: str) -> bool:
    comment_lines = [line.strip().lower() for line in fix.splitlines() if line.strip()]
    placeholders = (
        "# add ",
        "# replace ",
        "# implement ",
        "// add ",
        "// replace ",
        "// implement ",
    )
    return any(line.startswith(placeholders) for line in comment_lines)


def _introduces_unavailable_security_dependency(
    fix: str,
    *,
    source_text: str | None,
) -> bool:
    introduced = set(re.findall(r"\brequire_[A-Za-z_][A-Za-z0-9_]*\b", fix))
    if not introduced:
        return False
    available_text = "\n".join((source_text or "", fix))
    for name in introduced:
        if _security_dependency_available(name, available_text):
            continue
        return True
    return False


def _security_dependency_available(name: str, text: str) -> bool:
    patterns = (
        rf"\bimport\s+{re.escape(name)}\b",
        rf"\bimport\s+.*\b{re.escape(name)}\b",
        rf"\bdef\s+{re.escape(name)}\s*\(",
        rf"\bclass\s+{re.escape(name)}\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _source_text_by_path(pr_payload: Any | None) -> dict[str, str]:
    if pr_payload is None:
        return {}
    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list):
        return {}
    sources: dict[str, str] = {}
    for file_ in files:
        path = _normalise_path(str(getattr(file_, "path", "") or ""))
        source_text = getattr(file_, "source_text", None)
        if path and isinstance(source_text, str) and source_text:
            sources[path] = source_text
    return sources


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
