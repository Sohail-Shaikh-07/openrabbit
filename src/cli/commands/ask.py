"""Implementation of ``openrabbit ask --pr N "question"``.

The ask command is read-only. It fetches a pull request, optionally loads
repository context, asks the configured model provider a focused question, and
prints an evidence-based answer locally.
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
from knowledge.context import load_connector_context
from knowledge.diagnostics import build_context_precision_diagnostics
from memory.history import PullRequestHistory
from review_controls import prepare_review_controls

_log = get_logger(__name__)


@dataclass(frozen=True)
class AnswerEvidence:
    """One source used to support an ask-command answer."""

    source: str
    detail: str
    file: str = ""
    line: int | None = None


@dataclass(frozen=True)
class PullRequestAnswer:
    """Structured answer returned by the ask command."""

    answer: str
    evidence: list[AnswerEvidence] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    follow_up_checks: list[str] = field(default_factory=list)


AnswerGenerator = Callable[..., Awaitable[PullRequestAnswer]]

_PROMPT_TEMPLATE = """You are OpenRabbit's PR question-answering agent. Answer the user's question like a senior maintainer who has inspected the supplied pull request evidence.

Mission:
- Answer only the user's question, using the PR metadata, changed-line evidence, diff, and project context below.
- Separate what is directly supported from what is uncertain.
- Prefer "I cannot determine that from the provided evidence" over guessing.
- Cite concrete evidence from changed files, changed lines, metadata, or retrieved project context.
- Do not invent files, requirements, tests, tickets, runtime logs, production incidents, or unstated behavior.
- Keep the answer practical enough for a maintainer deciding what to inspect next.

User question:
{question}

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
  "answer": "Direct answer to the question. Say what is known and what cannot be determined.",
  "evidence": [
    {{
      "source": "diff|changed_lines|metadata|context",
      "file": "path/to/file.py",
      "line": 42,
      "detail": "Specific evidence supporting the answer."
    }}
  ],
  "uncertainty": ["Important limits or assumptions in the answer."],
  "follow_up_checks": ["Concrete thing the user can inspect or test next."]
}}
"""


async def run_ask(
    settings: Settings,
    *,
    number: int,
    question: str,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    generator: AnswerGenerator | None = None,
    context_loader: ContextLoader | None = None,
) -> dict[str, object]:
    """Fetch a PR, answer a question, and return a summary dict."""
    cleaned_question = _clean_text(question)
    if not cleaned_question:
        raise ValueError("question must not be empty")

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
            "ask.context_failed",
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
        query_extra=cleaned_question,
    )
    retrieval_result = connector_context.retrieval_result
    model_context = filter_model_review_context(
        controls_result,
        retrieval_result=retrieval_result,
        pr_history=pr_history_result.history,
    )
    retrieval_result = model_context.retrieval_result

    answerer = generator or _generate_answer
    answer = await answerer(
        payload,
        question=cleaned_question,
        settings=settings,
        retrieval_result=retrieval_result,
        pr_history=model_context.pr_history,
        env=env,
    )

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
            command="ask",
        ),
        "connector_context": connector_context.summary,
        "conversation_count": pr_history_result.conversation_count,
        "learning_count": pr_history_result.learning_count,
        "question": cleaned_question,
        "answer": _serialize_answer(answer),
    }


def run_ask_blocking(
    settings: Settings,
    *,
    number: int,
    question: str,
    repo: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(run_ask(settings, number=number, question=question, repo=repo, env=env))


def render_answer(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_ask`."""
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

    print("", file=out)
    print("Question:", file=out)
    print(f"  {summary.get('question', '')}", file=out)

    answer = summary.get("answer")
    if not isinstance(answer, dict):
        return

    print("", file=out)
    print("Answer:", file=out)
    print(f"  {answer.get('answer', '')}", file=out)
    _print_evidence(answer.get("evidence"), out)
    _print_list("Uncertainty:", answer.get("uncertainty"), out)
    _print_list("Follow-up checks:", answer.get("follow_up_checks"), out)


def render_answer_markdown(summary: dict[str, object], out: TextIO) -> None:
    """Render an ask response as Markdown."""
    print(f"# PR #{summary['number']} Ask", file=out)
    print("", file=out)
    _print_metadata(summary, out)
    print("", file=out)
    print("## Question", file=out)
    print("", file=out)
    print(str(summary.get("question", "")), file=out)

    answer = summary.get("answer")
    if not isinstance(answer, dict):
        return

    print("", file=out)
    print("## Answer", file=out)
    print("", file=out)
    print(str(answer.get("answer", "")), file=out)
    _print_markdown_evidence(answer.get("evidence"), out)
    _print_markdown_list("Uncertainty", answer.get("uncertainty"), out)
    _print_markdown_list("Follow-up Checks", answer.get("follow_up_checks"), out)


def render_answer_json(summary: dict[str, object], out: TextIO) -> None:
    """Render an ask response as deterministic JSON."""
    render_json(summary, out)


async def _generate_answer(
    pr_payload: Any,
    *,
    question: str,
    settings: Settings,
    retrieval_result: Any | None,
    pr_history: PullRequestHistory | None = None,
    env: dict[str, str] | None,
) -> PullRequestAnswer:
    client = _build_ask_client(settings, env=env)
    prompt = _build_prompt(pr_payload, question, retrieval_result, pr_history)
    raw = await client.generate(prompt)
    return _parse_answer(raw)


def _build_ask_client(settings: Settings, *, env: dict[str, str] | None) -> LLMClient:
    api_key = (
        settings.resolved_model_api_key(env=env) if settings.model.provider != "ollama" else None
    )
    return build_llm_client(settings.model, api_key=api_key)


def _build_prompt(
    pr_payload: Any,
    question: str,
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
        state, "security", "architecture", "performance", "tests", "bug"
    )
    if project_context == NO_PROJECT_CONTEXT:
        project_context = "(No project context retrieved. Answer from the PR diff only.)"

    return _PROMPT_TEMPLATE.format(
        question=question,
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


def _parse_answer(raw: str) -> PullRequestAnswer:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return PullRequestAnswer(
            answer=_clean_text(raw) or "I cannot determine that from the provided evidence."
        )
    if not isinstance(data, dict):
        return PullRequestAnswer(answer="I cannot determine that from the provided evidence.")

    return PullRequestAnswer(
        answer=_clean_text(data.get("answer"))
        or "I cannot determine that from the provided evidence.",
        evidence=_evidence_list(data.get("evidence")),
        uncertainty=_string_list(data.get("uncertainty")),
        follow_up_checks=_string_list(data.get("follow_up_checks")),
    )


def _serialize_answer(answer: PullRequestAnswer) -> dict[str, object]:
    return {
        "answer": answer.answer,
        "evidence": [_serialize_evidence(item) for item in answer.evidence],
        "uncertainty": answer.uncertainty,
        "follow_up_checks": answer.follow_up_checks,
    }


def _serialize_evidence(evidence: AnswerEvidence) -> dict[str, object]:
    return {
        "source": evidence.source,
        "file": evidence.file,
        "line": evidence.line,
        "detail": evidence.detail,
    }


def _evidence_list(value: object) -> list[AnswerEvidence]:
    if not isinstance(value, list):
        return []

    evidence: list[AnswerEvidence] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source = _clean_text(item.get("source"))
        detail = _clean_text(item.get("detail"))
        file_ = _normalise_path(_clean_text(item.get("file")))
        line = _line_number(item.get("line"))
        if source and detail:
            evidence.append(
                AnswerEvidence(
                    source=source,
                    detail=detail,
                    file=file_,
                    line=line,
                )
            )
    return evidence


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            cleaned.append(text)
    return cleaned


def _line_number(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _normalise_path(path: str) -> str:
    clean = path.strip().replace("\\", "/")
    if clean.startswith(("a/", "b/")):
        clean = clean[2:]
    return clean.lstrip("/")


def _print_evidence(value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print("Evidence:", file=out)
    for item in items:
        if not isinstance(item, dict):
            continue
        file_ = item.get("file")
        line = item.get("line")
        location = ""
        if isinstance(file_, str) and file_:
            location = file_
            if isinstance(line, int):
                location = f"{location}:{line}"
        source = item.get("source", "")
        detail = item.get("detail", "")
        prefix = f"[{source}]"
        if location:
            prefix = f"{prefix} {location}"
        print(f"  - {prefix}: {detail}", file=out)


def _print_list(title: str, value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print(title, file=out)
    for item in items:
        print(f"  - {item}", file=out)


def _print_metadata(summary: dict[str, object], out: TextIO) -> None:
    context_loaded = summary.get("context_loaded")
    context = (
        "loaded"
        if isinstance(context_loaded, bool) and context_loaded
        else "diff only" if isinstance(context_loaded, bool) else "unknown"
    )
    rows = [
        ("Repository", summary.get("repo", "")),
        ("Title", summary.get("title", "")),
        ("State", summary.get("state", "")),
        ("Head SHA", summary.get("head_sha", "")),
        ("Files", f"{summary.get('files_changed', 0)} ({summary.get('binary_files', 0)} binary)"),
        ("Hunks", summary.get("hunks", "")),
        ("Commits", summary.get("commits", "")),
        ("Context", context),
    ]
    for label, value in rows:
        print(f"- {label}: {value}", file=out)


def _print_markdown_evidence(value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print("## Evidence", file=out)
    print("", file=out)
    for item in items:
        if not isinstance(item, dict):
            continue
        file_ = item.get("file")
        line = item.get("line")
        location = ""
        if isinstance(file_, str) and file_:
            location = file_
            if isinstance(line, int):
                location = f"{location}:{line}"
        source = item.get("source", "")
        detail = item.get("detail", "")
        if location:
            print(f"- `{source}` `{location}`: {detail}", file=out)
        else:
            print(f"- `{source}`: {detail}", file=out)


def _print_markdown_list(title: str, value: object, out: TextIO) -> None:
    items = value if isinstance(value, list) else []
    if not items:
        return
    print("", file=out)
    print(f"## {title}", file=out)
    print("", file=out)
    for item in items:
        print(f"- {item}", file=out)
