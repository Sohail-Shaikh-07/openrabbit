"""Release changelog draft command."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import TextIO

from cli.commands.maintenance_controls import (
    read_only_workflow_controls,
    render_workflow_control_lines,
)
from cli.commands.output import render_json
from cli.commands.start import resolve_target_repo
from configs.settings import Settings
from github_ import GitHubClient, PullRequestSummary, RepositoryHandle
from knowledge.connectors import sanitize_knowledge_text

_SECTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Breaking Changes", ("breaking-change", "breaking", "major")),
    ("Features", ("feature", "enhancement")),
    ("Fixes", ("bug", "fix")),
    ("Documentation", ("docs", "documentation")),
    ("Tests", ("tests", "test")),
    ("Maintenance", ("chore", "refactor", "infrastructure", "config", "release", "ci")),
)
_DEFAULT_SECTION = "Other"
_NOTE_PREVIEW_CHARS = 1200


@dataclass(frozen=True)
class ChangelogEntry:
    """One merged pull request included in the draft."""

    number: int
    title: str
    url: str
    author: str
    labels: list[str]
    merged_at: str
    section: str


async def run_changelog_draft(
    settings: Settings,
    *,
    repo: str | None = None,
    workspace: Path | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 30,
    scan_limit: int = 100,
    notes: Sequence[Path] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Fetch merged PR metadata and return a deterministic read-only changelog draft."""
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    if scan_limit < limit:
        raise ValueError("--scan-limit must be greater than or equal to --limit")

    since_dt = _parse_bound(since, option="--since", end_of_day=False)
    until_dt = _parse_bound(until, option="--until", end_of_day=True)
    if since_dt and until_dt and since_dt > until_dt:
        raise ValueError("--since must be earlier than or equal to --until")

    workspace_root = (workspace or Path(".")).resolve()
    note_sources = _load_notes(workspace_root, notes)
    target_repo = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    try:
        handle = RepositoryHandle.from_full_name(target_repo, client)
        pull_requests = await handle.list_pull_requests(
            state="closed",
            max_items=scan_limit,
        )
    finally:
        await client.aclose()

    merged_prs = _select_merged_prs(
        pull_requests,
        since=since_dt,
        until=until_dt,
        limit=limit,
    )
    entries = [_entry_from_pr(pr) for pr in merged_prs]
    sections = _sections(entries)
    return {
        "schema_version": "1.0",
        "command": "changelog",
        "repo": target_repo,
        "since": since,
        "until": until,
        "limit": limit,
        "scan_limit": scan_limit,
        "source_pr_count": len(pull_requests),
        "merged_pr_count": len(merged_prs),
        "notes_loaded": len(note_sources),
        "notes": note_sources,
        "sections": sections,
        "workflow_controls": read_only_workflow_controls(
            required_permissions=("pull_requests:read",),
        ),
        "mutates_files": False,
        "mutates_github": False,
    }


def run_changelog_draft_blocking(
    settings: Settings,
    *,
    repo: str | None = None,
    workspace: Path | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 30,
    scan_limit: int = 100,
    notes: Sequence[Path] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(
        run_changelog_draft(
            settings,
            repo=repo,
            workspace=workspace,
            since=since,
            until=until,
            limit=limit,
            scan_limit=scan_limit,
            notes=notes,
            env=env,
        )
    )


def render_changelog_draft(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_changelog_draft`."""
    print(f"Changelog draft for {summary['repo']}", file=out)
    if summary.get("since") or summary.get("until"):
        print(
            f"Range: {summary.get('since') or 'beginning'} to " f"{summary.get('until') or 'now'}",
            file=out,
        )
    print(f"Merged PRs: {summary['merged_pr_count']}", file=out)
    if not render_workflow_control_lines(summary, out):
        print("GitHub write: no", file=out)
        print("File write:   no", file=out)

    notes = summary.get("notes")
    if isinstance(notes, list) and notes:
        print("", file=out)
        print("Release notes context:", file=out)
        for note in notes:
            if not isinstance(note, dict):
                continue
            suffix = " (truncated)" if note.get("truncated") else ""
            print(f"- {note.get('path', 'unknown')}{suffix}", file=out)

    sections = summary.get("sections")
    if not isinstance(sections, list) or not sections:
        print("", file=out)
        print("No merged pull requests matched the selected range.", file=out)
        return

    for section in sections:
        if not isinstance(section, dict):
            continue
        entries = section.get("entries")
        if not isinstance(entries, list) or not entries:
            continue
        print("", file=out)
        print(f"## {section.get('name', _DEFAULT_SECTION)}", file=out)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            labels = entry.get("labels")
            label_text = f" [{', '.join(labels)}]" if isinstance(labels, list) and labels else ""
            print(
                f"- {entry.get('title', 'Untitled')} " f"(#{entry.get('number', '?')}){label_text}",
                file=out,
            )


def render_changelog_draft_json(summary: dict[str, object], out: TextIO) -> None:
    """Render a changelog draft as deterministic JSON."""
    render_json(summary, out)


def _select_merged_prs(
    pull_requests: Sequence[PullRequestSummary],
    *,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> list[PullRequestSummary]:
    merged: list[PullRequestSummary] = []
    for pr in pull_requests:
        if pr.merged_at is None:
            continue
        merged_at = _ensure_utc(pr.merged_at)
        if since and merged_at < since:
            continue
        if until and merged_at > until:
            continue
        merged.append(pr)
    return sorted(
        merged,
        key=lambda item: (_ensure_utc(item.merged_at or item.updated_at), item.number),
        reverse=True,
    )[:limit]


def _entry_from_pr(pr: PullRequestSummary) -> ChangelogEntry:
    labels = sorted(
        {label.name.strip() for label in pr.labels if label.name.strip()}, key=str.lower
    )
    section = _section_for_labels(labels)
    return ChangelogEntry(
        number=pr.number,
        title=sanitize_knowledge_text(pr.title, max_chars=180),
        url=pr.html_url or "",
        author=pr.user.login,
        labels=labels,
        merged_at=_format_dt(_ensure_utc(pr.merged_at or pr.updated_at)),
        section=section,
    )


def _sections(entries: Sequence[ChangelogEntry]) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    for name in [rule[0] for rule in _SECTION_RULES] + [_DEFAULT_SECTION]:
        section_entries = [entry for entry in entries if entry.section == name]
        if not section_entries:
            continue
        sections.append(
            {
                "name": name,
                "entries": [
                    {
                        "number": entry.number,
                        "title": entry.title,
                        "url": entry.url,
                        "author": entry.author,
                        "labels": entry.labels,
                        "merged_at": entry.merged_at,
                    }
                    for entry in section_entries
                ],
            }
        )
    return sections


def _section_for_labels(labels: Sequence[str]) -> str:
    normalized = {_normalize_label(label) for label in labels}
    for section, candidates in _SECTION_RULES:
        if normalized.intersection(candidates):
            return section
    return _DEFAULT_SECTION


def _load_notes(workspace: Path, notes: Sequence[Path] | None) -> list[dict[str, object]]:
    if not notes:
        return []
    sources: list[dict[str, object]] = []
    for note_path in notes:
        resolved = note_path if note_path.is_absolute() else workspace / note_path
        resolved = resolved.resolve()
        if not resolved.exists():
            raise ValueError(f"release notes file does not exist: {note_path}")
        if not resolved.is_file():
            raise ValueError(f"release notes path is not a file: {note_path}")
        raw = resolved.read_text(encoding="utf-8", errors="replace")
        excerpt = sanitize_knowledge_text(raw, max_chars=_NOTE_PREVIEW_CHARS)
        sources.append(
            {
                "path": _display_path(workspace, resolved),
                "excerpt": excerpt,
                "truncated": len(" ".join(raw.strip().split())) > len(excerpt),
            }
        )
    return sources


def _parse_bound(value: str | None, *, option: str, end_of_day: bool) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            parsed_date = datetime.strptime(text, "%Y-%m-%d").date()
            parsed_time = time.max if end_of_day else time.min
            return datetime.combine(parsed_date, parsed_time, tzinfo=UTC)
        return _ensure_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        raise ValueError(f"{option} must be YYYY-MM-DD or an ISO datetime") from None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_dt(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _normalize_label(label: str) -> str:
    return " ".join(label.strip().lower().split())


def _display_path(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()
