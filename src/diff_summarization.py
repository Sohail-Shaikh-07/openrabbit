"""Deterministic helpers for summarizing large low-risk PR file changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RISKY_PATH_MARKERS = (
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
CODE_SUFFIXES = (
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
LOW_SIGNAL_SUFFIXES = (
    ".md",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".lock",
)

LOW_RISK_LARGE_CHANGE_THRESHOLD = 120
LOW_RISK_LARGE_CHAR_THRESHOLD = 6000
LOW_RISK_PREVIEW_LINES = 4
LOW_RISK_PREVIEW_CHARS = 180


@dataclass(frozen=True)
class LargeLowRiskFileSummary:
    """Bounded summary for an oversized file that is unlikely to need full review context."""

    path: str
    status: str
    additions: int
    deletions: int
    changes: int
    hunks: int
    diff_lines: int
    reasons: tuple[str, ...]
    added_preview: tuple[str, ...]
    deleted_preview: tuple[str, ...]


def summarize_large_low_risk_file(file_: Any) -> LargeLowRiskFileSummary | None:
    """Return a deterministic summary when a changed file is oversized and low-risk."""
    if bool(getattr(file_, "is_binary", False)):
        return None

    path = file_path(file_)
    lowered = path.lower()
    if _is_risky_path(lowered):
        return None
    if not (_is_low_signal_path(lowered) or not _is_code_path(lowered)):
        return None

    additions, deletions, changes = file_counts(file_)
    diff_lines = _diff_line_count(file_)
    diff_chars = _diff_body_chars(file_)
    oversized = (
        changes >= LOW_RISK_LARGE_CHANGE_THRESHOLD
        or diff_lines >= LOW_RISK_LARGE_CHANGE_THRESHOLD
        or diff_chars >= LOW_RISK_LARGE_CHAR_THRESHOLD
    )
    if not oversized:
        return None

    return LargeLowRiskFileSummary(
        path=path,
        status=str(getattr(file_, "status", "") or "modified"),
        additions=additions,
        deletions=deletions,
        changes=changes or additions + deletions,
        hunks=_hunk_count(file_),
        diff_lines=diff_lines,
        reasons=_summary_reasons(lowered, changes, diff_lines, diff_chars),
        added_preview=_line_preview(file_, kind="addition"),
        deleted_preview=_line_preview(file_, kind="deletion"),
    )


def file_path(file_: Any) -> str:
    """Return the normalized path for a parsed PR file object."""
    return str(getattr(file_, "path", "") or getattr(getattr(file_, "file", None), "filename", ""))


def file_counts(file_: Any) -> tuple[int, int, int]:
    """Return additions, deletions, and changes from parsed or API file metadata."""
    api_file = getattr(file_, "file", None)
    additions = _int_attr(file_, "additions", api_file)
    deletions = _int_attr(file_, "deletions", api_file)
    changes = _int_attr(file_, "changes", api_file)
    if changes == 0:
        changes = additions + deletions
    return additions, deletions, changes


def file_change_count(file_: Any) -> int:
    """Return the best available changed-line count for a parsed PR file object."""
    additions, deletions, changes = file_counts(file_)
    return changes or additions + deletions


def _is_risky_path(lowered_path: str) -> bool:
    return any(marker in lowered_path for marker in RISKY_PATH_MARKERS)


def _is_code_path(lowered_path: str) -> bool:
    return lowered_path.endswith(CODE_SUFFIXES)


def _is_low_signal_path(lowered_path: str) -> bool:
    return lowered_path.endswith(LOW_SIGNAL_SUFFIXES)


def _summary_reasons(
    lowered_path: str,
    changes: int,
    diff_lines: int,
    diff_chars: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if _is_low_signal_path(lowered_path):
        reasons.append("low_signal_path")
    if not _is_code_path(lowered_path):
        reasons.append("non_code_path")
    if changes >= LOW_RISK_LARGE_CHANGE_THRESHOLD:
        reasons.append("large_change_count")
    if diff_lines >= LOW_RISK_LARGE_CHANGE_THRESHOLD:
        reasons.append("large_diff_line_count")
    if diff_chars >= LOW_RISK_LARGE_CHAR_THRESHOLD:
        reasons.append("large_diff_body")
    return tuple(dict.fromkeys(reasons))


def _line_preview(file_: Any, *, kind: str) -> tuple[str, ...]:
    preview: list[str] = []
    for line in _iter_hunk_lines(file_):
        if str(getattr(line, "kind", "")) != kind:
            continue
        text = _clean_preview_text(str(getattr(line, "text", "") or ""))
        if text:
            preview.append(text)
        if len(preview) >= LOW_RISK_PREVIEW_LINES:
            break
    return tuple(preview)


def _clean_preview_text(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= LOW_RISK_PREVIEW_CHARS:
        return cleaned
    return f"{cleaned[: LOW_RISK_PREVIEW_CHARS - 3].rstrip()}..."


def _diff_line_count(file_: Any) -> int:
    return sum(1 for line in _iter_hunk_lines(file_) if str(getattr(line, "kind", "")) != "")


def _diff_body_chars(file_: Any) -> int:
    return sum(len(str(getattr(line, "text", "") or "")) for line in _iter_hunk_lines(file_))


def _hunk_count(file_: Any) -> int:
    hunks = getattr(file_, "hunks", None)
    return len(hunks) if isinstance(hunks, list) else 0


def _iter_hunk_lines(file_: Any) -> list[Any]:
    hunks = getattr(file_, "hunks", None)
    if not isinstance(hunks, list):
        return []
    lines: list[Any] = []
    for hunk in hunks:
        hunk_lines = getattr(hunk, "lines", None)
        if isinstance(hunk_lines, list):
            lines.extend(hunk_lines)
    return lines


def _int_attr(file_: Any, name: str, api_file: Any) -> int:
    value = getattr(file_, name, None)
    if value is None and api_file is not None:
        value = getattr(api_file, name, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
