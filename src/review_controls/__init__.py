"""Review controls for path filtering and prompt guidance."""

from __future__ import annotations

import copy
import fnmatch
from dataclasses import dataclass, is_dataclass, replace
from typing import Any

from configs.schema import PathInstruction, ReviewSettings

_GENERATED_PATH_MARKERS = (
    "/generated/",
    "\\generated\\",
    "/dist/",
    "\\dist\\",
    "/build/",
    "\\build\\",
)
_GENERATED_SUFFIXES = (
    ".generated.py",
    ".generated.ts",
    ".generated.tsx",
    ".generated.js",
    ".generated.jsx",
    ".min.js",
    ".min.css",
    ".lock",
)
_GENERATED_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "cargo.lock",
}


@dataclass(frozen=True)
class SkippedPath:
    """One file skipped by review controls."""

    path: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True)
class ReviewControlResult:
    """Filtered payload plus review-control metadata."""

    filtered_payload: Any
    skipped_paths: list[SkippedPath]


def apply_review_controls(pr_payload: Any, settings: ReviewSettings) -> ReviewControlResult:
    """Return a PR payload filtered according to configured review controls."""
    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list):
        filtered_payload = _payload_with_files(pr_payload, [])
        _attach_control_metadata(filtered_payload, settings, [], [])
        return ReviewControlResult(filtered_payload=filtered_payload, skipped_paths=[])

    kept: list[Any] = []
    skipped: list[SkippedPath] = []
    changed_lines_used = 0

    for file_ in files:
        path = _file_path(file_)
        reason = _skip_reason(path, settings)
        if reason is not None:
            skipped.append(SkippedPath(path=path, reason=reason))
            continue

        changed_lines = _changed_line_count(file_)
        if changed_lines_used + changed_lines > settings.max_changed_lines:
            skipped.append(SkippedPath(path=path, reason="max_changed_lines"))
            continue

        if len(kept) >= settings.max_files:
            skipped.append(SkippedPath(path=path, reason="max_files"))
            continue

        kept.append(file_)
        changed_lines_used += changed_lines

    instructions = _matching_instructions(kept, settings.path_instructions)
    filtered_payload = _payload_with_files(pr_payload, kept)
    _attach_control_metadata(filtered_payload, settings, instructions, skipped)
    return ReviewControlResult(filtered_payload=filtered_payload, skipped_paths=skipped)


def format_review_control_context(pr_payload: Any) -> str:
    """Return prompt text describing active review controls."""
    profile = str(getattr(pr_payload, "openrabbit_review_profile", "") or "").strip()
    instructions = getattr(pr_payload, "openrabbit_path_instructions", None)
    skipped = getattr(pr_payload, "openrabbit_skipped_paths", None)
    if not profile and not instructions and not skipped:
        return ""

    lines = ["Review controls:"]
    if profile:
        lines.append(f"- Profile: {profile}")
        lines.append(f"- {profile_guidance(profile)}")
    if isinstance(instructions, list) and instructions:
        lines.append("- Path instructions:")
        for item in instructions:
            if isinstance(item, PathInstruction):
                lines.append(f"  - {item.path}: {item.instructions}")
    if isinstance(skipped, list) and skipped:
        lines.append(f"- Skipped paths: {len(skipped)} file(s) omitted by configured controls.")
    return "\n".join(lines)


def profile_guidance(profile: str) -> str:
    """Return prompt guidance for a review profile."""
    if profile == "chill":
        return "Focus on clear, high-confidence issues with practical merge risk."
    return "Surface concrete security, correctness, performance, architecture, and test risks with changed-line evidence."


def _skip_reason(path: str, settings: ReviewSettings) -> str | None:
    if settings.path_include and not _matches_any(path, settings.path_include):
        return "path_not_included"
    if _matches_any(path, settings.path_exclude):
        return "path_excluded"
    if not settings.include_generated and _is_generated_path(path):
        return "generated"
    return None


def _matching_instructions(
    files: list[Any],
    instructions: list[PathInstruction],
) -> list[PathInstruction]:
    paths = [_file_path(file_) for file_ in files]
    return [
        instruction
        for instruction in instructions
        if any(_matches(path, instruction.path) for path in paths)
    ]


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_matches(path, pattern) for pattern in patterns)


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.rstrip("/") + "/**")


def _is_generated_path(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return (
        name in _GENERATED_FILENAMES
        or lowered.endswith(_GENERATED_SUFFIXES)
        or any(marker in lowered for marker in _GENERATED_PATH_MARKERS)
    )


def _changed_line_count(file_: Any) -> int:
    changes = _int_attr(file_, "changes")
    if changes:
        return changes
    return _int_attr(file_, "additions") + _int_attr(file_, "deletions")


def _int_attr(file_: Any, name: str) -> int:
    value = getattr(file_, name, None)
    api_file = getattr(file_, "file", None)
    if value is None and api_file is not None:
        value = getattr(api_file, name, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _file_path(file_: Any) -> str:
    return str(getattr(file_, "path", "") or getattr(getattr(file_, "file", None), "filename", ""))


def _payload_with_files(pr_payload: Any, files: list[Any]) -> Any:
    if is_dataclass(pr_payload):
        return replace(pr_payload, files=files)  # type: ignore[type-var]
    filtered = copy.copy(pr_payload)
    filtered.files = files
    return filtered


def _attach_control_metadata(
    payload: Any,
    settings: ReviewSettings,
    instructions: list[PathInstruction],
    skipped: list[SkippedPath],
) -> None:
    object.__setattr__(payload, "openrabbit_review_profile", settings.profile)
    object.__setattr__(payload, "openrabbit_path_instructions", instructions)
    object.__setattr__(payload, "openrabbit_skipped_paths", [item.as_dict() for item in skipped])
