"""Prompt helpers shared by OpenRabbit review agents."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agents.models import ReviewState

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


def format_changed_line_evidence(
    pr_payload: Any,
    *,
    max_files: int = 12,
    max_lines_per_file: int = 80,
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

    return "\n".join(lines)


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
