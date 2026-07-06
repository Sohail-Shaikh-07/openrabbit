"""Prompt helpers shared by OpenRabbit review agents."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from agents.models import ReviewState
from memory.history import PullRequestHistory, format_history_context
from review_controls import format_review_control_context

REVIEW_DISCIPLINE = """Review discipline:
- Prioritize high-signal findings that a senior maintainer would act on before merge.
- Anchor every finding to evidence visible in the diff or project context.
- Do not invent missing files, unseen call paths, dependencies, requirements, or runtime behavior.
- Prefer no finding over a speculative or stylistic comment.
- Report issues on changed lines unless unchanged surrounding code proves the changed line introduces or exposes the problem.
- Inspect the changed-line evidence before the full diff. For line-level findings, the file and line should appear in that evidence.
- Keep suggestions concrete, minimal, and compatible with the surrounding code style.
- Do not flag formatting, naming, or preference-only concerns unless the project context makes them mandatory.
"""

JSON_RESPONSE_CONTRACT = """Reply with ONLY a JSON object in this exact format, no prose:
{
  "findings": [
    {
      "severity": "critical|high|medium|low",
      "file": "path/to/file.py",
      "line": 42,
      "confidence": 0.85,
      "title": "Short title",
      "reason": "Why this matters and what evidence supports it.",
      "suggestion": "How to fix it.",
      "fix": "Optional corrected code snippet"
    }
  ]
}
"""

NO_PROJECT_CONTEXT = "(No project context retrieved for this review.)"
NO_CHANGED_LINE_EVIDENCE = "(No changed-line evidence available.)"
NO_DIFF = "(No diff available.)"

APPROX_CHARS_PER_TOKEN = 4
DEFAULT_DIFF_TOKEN_BUDGET = 6000
DEFAULT_CHANGED_LINE_EVIDENCE_TOKEN_BUDGET = 3000

_RISKY_PATH_MARKERS = (
    "auth",
    "authorization",
    "permission",
    "policy",
    "admin",
    "security",
    "secret",
    "token",
    "credential",
    "password",
    "session",
    "cookie",
    "payment",
    "billing",
    "webhook",
    "sql",
    "query",
    "migration",
    "database",
    "db",
    "crypto",
    "cors",
)
_CODE_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".rb",
    ".php",
    ".sql",
)
_LOW_SIGNAL_SUFFIXES = (
    ".md",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".lock",
)


def estimate_prompt_tokens(text: str) -> int:
    """Return a deterministic token estimate for prompt budgeting.

    The local providers OpenRabbit supports do not expose a shared tokenizer.
    A four-characters-per-token estimate is conservative enough for prompt
    packing and stable across environments.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / APPROX_CHARS_PER_TOKEN))


def collect_context(state: ReviewState, *dimensions: str) -> str:
    """Return formatted retrieved context for the requested dimensions."""
    retrieval = state.get("retrieval_result")
    if retrieval is None:
        return NO_PROJECT_CONTEXT

    items: list[Any] = []
    for dimension in dimensions:
        value = getattr(retrieval, dimension, None)
        if isinstance(value, list):
            items.extend(value)

    return format_context(items)


def collect_history_context(state: ReviewState) -> str:
    """Return formatted PR memory and conversation context."""
    history = state.get("pr_history")
    if isinstance(history, PullRequestHistory):
        return format_history_context(history)
    return format_history_context(history)


def format_changed_line_evidence(
    pr_payload: Any,
    *,
    max_files: int = 12,
    max_lines_per_file: int = 80,
    max_tokens: int = DEFAULT_CHANGED_LINE_EVIDENCE_TOKEN_BUDGET,
) -> str:
    """Return compact prompt evidence for added lines in parsed PR hunks."""
    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list) or not files:
        return NO_CHANGED_LINE_EVIDENCE

    lines: list[str] = ["Changed-line evidence:"]
    files_with_additions = 0
    omitted_files = 0

    for file_ in files:
        if bool(getattr(file_, "is_binary", False)):
            continue

        additions = _changed_lines_for_file(file_)
        if not additions:
            continue

        files_with_additions += 1
        if files_with_additions > max_files:
            omitted_files += 1
            continue

        path = str(
            getattr(file_, "path", "") or getattr(getattr(file_, "file", None), "filename", "")
        )
        status = str(getattr(file_, "status", "") or "modified")
        lines.append(f"{path} ({status}):")

        visible = additions[:max_lines_per_file]
        for line_number, text in visible:
            lines.append(f"  +{line_number} {text}")

        omitted_lines = len(additions) - len(visible)
        if omitted_lines > 0:
            noun = "line" if omitted_lines == 1 else "lines"
            lines.append(f"  ... {omitted_lines} additional added {noun} omitted.")

    if files_with_additions == 0:
        return NO_CHANGED_LINE_EVIDENCE

    if omitted_files > 0:
        noun = "file" if omitted_files == 1 else "files"
        lines.append(f"... {omitted_files} additional changed {noun} omitted.")

    return _truncate_at_line_boundary(
        "\n".join(lines),
        max_chars=_token_budget_to_chars(max_tokens),
        note="... additional changed-line evidence omitted to keep the prompt within budget.",
    )


def format_prompt_diff(
    pr_payload: Any,
    *,
    max_tokens: int = DEFAULT_DIFF_TOKEN_BUDGET,
) -> str:
    """Return a token-aware prompt diff for review agents.

    Small test-provided raw diffs are preserved exactly. Real GitHub payloads
    are rebuilt from parsed hunks because they do not carry a single combined
    diff string, and large diffs are packed deterministically by file priority.
    """
    max_chars = _token_budget_to_chars(max_tokens)
    control_context = format_review_control_context(pr_payload)
    raw_diff = _raw_diff(pr_payload)
    if raw_diff and len(raw_diff) <= max_chars:
        return _prepend_control_context(control_context, raw_diff, max_chars=max_chars)

    files = getattr(pr_payload, "files", None)
    if isinstance(files, list) and files:
        return _prepend_control_context(
            control_context,
            _format_structured_diff(files, max_chars=max_chars),
            max_chars=max_chars,
        )

    if raw_diff:
        return _prepend_control_context(
            control_context,
            _truncate_at_line_boundary(
                raw_diff,
                max_chars=max_chars,
                note="... raw diff omitted to keep the prompt within budget.",
            ),
            max_chars=max_chars,
        )

    return _prepend_control_context(control_context, NO_DIFF, max_chars=max_chars)


def format_context(items: Iterable[Any]) -> str:
    """Format RAG hits or test-provided strings into prompt-ready context."""
    lines: list[str] = []
    for item in items:
        source, text = _context_item_parts(item)
        if not text:
            continue
        clean = " ".join(text.split())
        if source:
            lines.append(f"- [{source}] {clean}")
        else:
            lines.append(f"- {clean}")

    if not lines:
        return NO_PROJECT_CONTEXT
    return "\n".join(lines)


def _changed_lines_for_file(file_: Any) -> list[tuple[int, str]]:
    additions: list[tuple[int, str]] = []
    hunks = getattr(file_, "hunks", None)
    if not isinstance(hunks, list):
        return additions

    for hunk in hunks:
        new_line = int(getattr(hunk, "new_start", 0) or 0)
        hunk_lines = getattr(hunk, "lines", None)
        if not isinstance(hunk_lines, list):
            continue

        for line in hunk_lines:
            kind = getattr(line, "kind", "")
            text = str(getattr(line, "text", ""))
            if kind == "addition":
                additions.append((new_line, text))
                new_line += 1
            elif kind == "context":
                new_line += 1
            elif kind == "deletion":
                continue

    return additions


def _format_structured_diff(files: list[Any], *, max_chars: int) -> str:
    lines: list[str] = [
        "Compressed diff:",
        (
            "OpenRabbit rebuilt this diff from parsed GitHub hunks and prioritized "
            "changed code files within the prompt budget."
        ),
    ]
    used = len("\n".join(lines))
    omitted_files = 0
    omitted_lines = 0

    for file_ in sorted(files, key=_file_priority_key):
        section_lines, section_omitted_lines = _diff_section_for_file(file_)
        if not section_lines:
            omitted_files += 1
            continue

        section = "\n".join(section_lines)
        projected = used + len(section) + 1
        if projected <= max_chars:
            lines.extend(section_lines)
            used = projected
            omitted_lines += section_omitted_lines
            continue

        remaining = max_chars - used
        partial_lines, partial_omitted_lines = _fit_lines(section_lines, remaining)
        if partial_lines:
            lines.extend(partial_lines)
            omitted_lines += section_omitted_lines + partial_omitted_lines
        else:
            omitted_lines += section_omitted_lines + _count_diff_body_lines(section_lines)
        omitted_files += 1

    if omitted_files > 0 or omitted_lines > 0:
        file_noun = "file" if omitted_files == 1 else "files"
        line_noun = "line" if omitted_lines == 1 else "lines"
        lines.append(
            f"... OpenRabbit omitted {omitted_files} {file_noun} and "
            f"{omitted_lines} diff {line_noun} to keep the prompt within budget."
        )

    result = "\n".join(lines)
    if len(result) <= max_chars:
        return result
    return _truncate_at_line_boundary(
        result,
        max_chars=max_chars,
        note="... compressed diff omitted to keep the prompt within budget.",
    )


def _diff_section_for_file(file_: Any) -> tuple[list[str], int]:
    path = _file_path(file_)
    status = str(getattr(file_, "status", "") or "modified")
    additions, deletions, changes = _file_counts(file_)
    lines = [
        f"diff --git a/{path} b/{path}",
        f"# status: {status}; additions: {additions}; deletions: {deletions}; changes: {changes}",
    ]

    if bool(getattr(file_, "is_binary", False)):
        lines.append("# binary, renamed-without-patch, or too-large patch omitted by GitHub")
        return lines, 0

    hunks = getattr(file_, "hunks", None)
    if not isinstance(hunks, list) or not hunks:
        lines.append("# no textual hunks available")
        return lines, 0

    for hunk in hunks:
        old_start = int(getattr(hunk, "old_start", 0) or 0)
        old_lines = int(getattr(hunk, "old_lines", 0) or 0)
        new_start = int(getattr(hunk, "new_start", 0) or 0)
        new_lines = int(getattr(hunk, "new_lines", 0) or 0)
        lines.append(f"@@ -{old_start},{old_lines} +{new_start},{new_lines} @@")

        hunk_lines = getattr(hunk, "lines", None)
        if not isinstance(hunk_lines, list):
            continue
        for line in hunk_lines:
            kind = str(getattr(line, "kind", "context"))
            text = str(getattr(line, "text", ""))
            lines.append(f"{_diff_prefix(kind)}{text}")

    return lines, 0


def _fit_lines(lines: list[str], max_chars: int) -> tuple[list[str], int]:
    if max_chars <= 0:
        return [], _count_diff_body_lines(lines)

    fitted: list[str] = []
    used = 0
    for line in lines:
        projected = used + len(line) + (1 if fitted else 0)
        if projected > max_chars:
            break
        fitted.append(line)
        used = projected

    omitted = _count_diff_body_lines(lines[len(fitted) :])
    return fitted, omitted


def _count_diff_body_lines(lines: list[str]) -> int:
    return sum(
        1
        for line in lines
        if line.startswith(("+", "-", " ")) and not line.startswith(("+++", "---"))
    )


def _file_priority_key(file_: Any) -> tuple[bool, int, str]:
    path = _file_path(file_)
    lowered = path.lower()
    score = _file_change_count(file_)
    if any(marker in lowered for marker in _RISKY_PATH_MARKERS):
        score += 1000
    if lowered.endswith(_CODE_SUFFIXES):
        score += 200
    if lowered.endswith(_LOW_SIGNAL_SUFFIXES):
        score -= 200
    if "/test" in lowered or "\\test" in lowered or lowered.startswith("tests/"):
        score -= 50
    return (bool(getattr(file_, "is_binary", False)), -score, path)


def _file_path(file_: Any) -> str:
    return str(getattr(file_, "path", "") or getattr(getattr(file_, "file", None), "filename", ""))


def _file_counts(file_: Any) -> tuple[int, int, int]:
    api_file = getattr(file_, "file", None)
    additions = _int_attr(file_, "additions", api_file)
    deletions = _int_attr(file_, "deletions", api_file)
    changes = _int_attr(file_, "changes", api_file)
    if changes == 0:
        changes = additions + deletions
    return additions, deletions, changes


def _file_change_count(file_: Any) -> int:
    additions, deletions, changes = _file_counts(file_)
    return changes or additions + deletions


def _int_attr(file_: Any, name: str, api_file: Any) -> int:
    value = getattr(file_, name, None)
    if value is None and api_file is not None:
        value = getattr(api_file, name, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _diff_prefix(kind: str) -> str:
    if kind == "addition":
        return "+"
    if kind == "deletion":
        return "-"
    if kind == "no_newline_marker":
        return "\\ "
    return " "


def _raw_diff(pr_payload: Any) -> str:
    if pr_payload is None:
        return ""
    return str(getattr(pr_payload, "diff", "") or "")


def _token_budget_to_chars(max_tokens: int) -> int:
    return max(0, max_tokens * APPROX_CHARS_PER_TOKEN)


def _truncate_at_line_boundary(text: str, *, max_chars: int, note: str) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= len(note) + 1:
        return note[:max_chars]

    limit = max_chars - len(note) - 1
    head = text[:limit]
    boundary = head.rfind("\n")
    if boundary > 0:
        head = head[:boundary]
    return f"{head}\n{note}"


def _prepend_control_context(control_context: str, body: str, *, max_chars: int) -> str:
    if not control_context:
        return body
    combined = f"{control_context}\n\n{body}"
    return _truncate_at_line_boundary(
        combined,
        max_chars=max_chars,
        note="... review controls or diff omitted to keep the prompt within budget.",
    )


def _context_item_parts(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        return "", item

    if isinstance(item, dict):
        payload = item.get("payload")
        if isinstance(payload, dict):
            source = str(payload.get("source_path") or payload.get("name") or "")
            text = str(payload.get("text") or "")
            return source, text
        return "", str(item.get("text") or "")

    source = str(getattr(item, "source_path", "") or getattr(item, "name", "") or "")
    text = str(getattr(item, "text", "") or "")
    return source, text
