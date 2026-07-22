"""Shared prompt context packing budgets for model-facing commands."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

APPROX_CHARS_PER_TOKEN = 4

DEFAULT_SOURCE_TOKEN_BUDGETS: dict[str, int] = {
    "changed_line_evidence": 3000,
    "diff": 6000,
    "rag": 4500,
    "connector": 1200,
    "memory": 1600,
    "linked_issue": 1200,
    "quality": 2000,
}


@dataclass(frozen=True)
class ContextSection:
    """One source-specific chunk of prompt context before final packing."""

    source: str
    text: str
    candidate_items: int = 1
    omission_note: str = "... context omitted to keep the prompt within budget."


@dataclass(frozen=True)
class PackedContext:
    """Prompt-ready context plus deterministic packing diagnostics."""

    text: str
    diagnostics: dict[str, dict[str, object]]


def estimate_tokens(text: str) -> int:
    """Return a deterministic token estimate for prompt budgeting."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / APPROX_CHARS_PER_TOKEN))


def token_budget_to_chars(max_tokens: int) -> int:
    """Convert an approximate token budget into a character budget."""
    return max(0, max_tokens * APPROX_CHARS_PER_TOKEN)


def source_budget_tokens(source: str) -> int:
    """Return the default token budget for a context source."""
    return DEFAULT_SOURCE_TOKEN_BUDGETS.get(source, 0)


def pack_context_sections(
    sections: Iterable[ContextSection],
    *,
    budgets: Mapping[str, int] | None = None,
    separator: str = "\n\n",
) -> PackedContext:
    """Pack source-specific sections under explicit per-source token budgets."""
    token_budgets = dict(DEFAULT_SOURCE_TOKEN_BUDGETS)
    if budgets is not None:
        token_budgets.update({str(key): int(value) for key, value in budgets.items()})

    packed: list[str] = []
    diagnostics: dict[str, dict[str, object]] = {}
    for section in sections:
        if not section.text:
            continue
        max_tokens = max(0, int(token_budgets.get(section.source, 0)))
        max_chars = token_budget_to_chars(max_tokens)
        selected = _truncate_at_line_boundary(
            section.text,
            max_chars=max_chars,
            note=section.omission_note,
        )
        selected_items = section.candidate_items if selected == section.text else 0
        dropped_items = 0 if selected == section.text else max(1, section.candidate_items)
        if selected:
            packed.append(selected)
        diagnostics[section.source] = {
            "max_tokens": max_tokens,
            "candidate_items": max(0, section.candidate_items),
            "selected_items": selected_items,
            "dropped_items": dropped_items,
            "selected_chars": len(selected),
            "estimated_tokens": estimate_tokens(selected),
        }

    return PackedContext(text=separator.join(packed), diagnostics=diagnostics)


def _truncate_at_line_boundary(text: str, *, max_chars: int, note: str) -> str:
    if max_chars <= 0:
        return ""
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
