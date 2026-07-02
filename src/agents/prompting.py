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
