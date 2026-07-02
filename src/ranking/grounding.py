"""Ground model findings against the parsed pull request diff."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.models import Finding
from github_.diff import Hunk


@dataclass(frozen=True)
class DroppedFinding:
    """A finding rejected by the grounding pass."""

    finding: Finding
    reason: str


@dataclass(frozen=True)
class GroundingResult:
    """Findings that survived grounding plus rejected findings."""

    kept: list[Finding]
    dropped: list[DroppedFinding]


@dataclass(frozen=True)
class DiffGroundingIndex:
    """Changed files and changed new-side lines for one pull request."""

    changed_files: frozenset[str]
    changed_lines: dict[str, frozenset[int]]


def filter_grounded_findings(findings: list[Finding], pr_payload: Any) -> GroundingResult:
    """Keep only findings that point at files and lines changed by the PR."""
    index = build_diff_grounding_index(pr_payload)
    if not index.changed_files:
        return GroundingResult(kept=list(findings), dropped=[])

    kept: list[Finding] = []
    dropped: list[DroppedFinding] = []
    for finding in findings:
        file_path = _normalise_path(finding.file)
        if file_path not in index.changed_files:
            dropped.append(DroppedFinding(finding=finding, reason="file_not_changed"))
            continue

        if finding.line <= 0:
            kept.append(finding)
            continue

        if finding.line not in index.changed_lines.get(file_path, frozenset()):
            dropped.append(DroppedFinding(finding=finding, reason="line_not_changed"))
            continue

        kept.append(finding)

    return GroundingResult(kept=kept, dropped=dropped)


def build_diff_grounding_index(pr_payload: Any) -> DiffGroundingIndex:
    """Build changed file and added-line metadata from a parsed PR payload."""
    changed_files: set[str] = set()
    changed_lines: dict[str, set[int]] = {}

    files = getattr(pr_payload, "files", None)
    if not isinstance(files, list):
        return DiffGroundingIndex(changed_files=frozenset(), changed_lines={})

    for parsed_file in files:
        path = _normalise_path(str(getattr(parsed_file, "path", "") or ""))
        if not path:
            continue
        changed_files.add(path)
        line_set = changed_lines.setdefault(path, set())
        for hunk in getattr(parsed_file, "hunks", []) or []:
            if isinstance(hunk, Hunk):
                line_set.update(_added_lines(hunk))

    return DiffGroundingIndex(
        changed_files=frozenset(changed_files),
        changed_lines={path: frozenset(lines) for path, lines in changed_lines.items()},
    )


def _added_lines(hunk: Hunk) -> set[int]:
    changed: set[int] = set()
    new_line = hunk.new_start
    for line in hunk.lines:
        if line.kind == "addition":
            changed.add(new_line)
            new_line += 1
        elif line.kind == "context":
            new_line += 1
        elif line.kind == "deletion":
            continue
    return changed


def _normalise_path(path: str) -> str:
    clean = path.strip().replace("\\", "/")
    if clean.startswith("a/") or clean.startswith("b/"):
        clean = clean[2:]
    return clean.lstrip("/")
