"""Local PR memory inspection command."""

from __future__ import annotations

from collections import Counter
from typing import TextIO

from cli.commands.start import resolve_target_repo
from configs.settings import Settings
from memory.models import FindingMemoryRecord
from memory.store import SQLitePullRequestMemory


def run_memory_inspect(
    settings: Settings,
    *,
    repo: str | None,
    pr_number: int,
) -> dict[str, object]:
    """Return a read-only summary of local memory for one pull request."""
    if pr_number <= 0:
        raise ValueError("PR number must be a positive integer")

    target_repo = resolve_target_repo(settings, repo)
    memory_path = settings.resolved_memory_path()
    if not memory_path.is_file():
        return {
            "repo": target_repo,
            "pr_number": pr_number,
            "memory_enabled": settings.memory.enabled,
            "memory_path": str(memory_path),
            "memory_database_exists": False,
            "last_reviewed_sha": None,
            "findings_count": 0,
            "status_counts": {},
            "findings": [],
        }

    store = SQLitePullRequestMemory(memory_path)
    history = store.load_history(target_repo, pr_number)
    findings = [_finding_record(record) for record in history.previous_findings]
    status_counts = Counter(str(item["status"]) for item in findings)
    return {
        "repo": target_repo,
        "pr_number": pr_number,
        "memory_enabled": settings.memory.enabled,
        "memory_path": str(memory_path),
        "memory_database_exists": True,
        "last_reviewed_sha": history.last_reviewed_sha,
        "findings_count": len(findings),
        "status_counts": dict(sorted(status_counts.items())),
        "findings": findings,
    }


def render_memory_summary(summary: dict[str, object], out: TextIO) -> None:
    """Print a compact local memory report."""
    print("OpenRabbit memory", file=out)
    print(f"  Repo:        {summary.get('repo')}", file=out)
    print(f"  PR:          #{summary.get('pr_number')}", file=out)
    print(f"  Memory:      {_enabled_text(summary.get('memory_enabled'))}", file=out)
    print(f"  Database:    {summary.get('memory_path')}", file=out)

    if summary.get("memory_database_exists") is not True:
        print("  Status:      database not found", file=out)
        print("No local memory has been recorded for this workspace yet.", file=out)
        return

    last_sha = summary.get("last_reviewed_sha") or "none"
    print(f"  Last SHA:    {last_sha}", file=out)
    print(f"  Findings:    {summary.get('findings_count', 0)}", file=out)
    statuses = _status_text(summary.get("status_counts"))
    if statuses:
        print(f"  Statuses:    {statuses}", file=out)

    findings = summary.get("findings")
    if not isinstance(findings, list) or not findings:
        print("No findings are stored for this pull request.", file=out)
        return

    print("", file=out)
    print("Stored findings:", file=out)
    for item in findings:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "unknown")).upper()
        title = str(item.get("title", "Untitled finding"))
        category = str(item.get("category", "unknown"))
        severity = str(item.get("severity", "unknown")).upper()
        print(f"  - [{status}] {title} ({category}/{severity})", file=out)
        print(f"    {item.get('file')}:{item.get('line')}", file=out)
        print(f"    fingerprint: {item.get('fingerprint')}", file=out)
        print(
            f"    first seen: {item.get('first_seen_sha')} at {item.get('first_seen_at')}",
            file=out,
        )
        print(
            f"    last seen:  {item.get('last_seen_sha')} at {item.get('last_seen_at')}",
            file=out,
        )


def _finding_record(record: FindingMemoryRecord) -> dict[str, object]:
    return {
        "fingerprint": record.fingerprint,
        "status": record.status.value,
        "title": record.title,
        "category": record.category,
        "severity": record.severity,
        "file": record.file,
        "line": record.line,
        "reason": record.reason,
        "suggestion": record.suggestion,
        "first_seen_sha": record.first_seen_sha,
        "last_seen_sha": record.last_seen_sha,
        "first_seen_at": record.first_seen_at.isoformat(),
        "last_seen_at": record.last_seen_at.isoformat(),
    }


def _enabled_text(value: object) -> str:
    if value is True:
        return "enabled"
    if value is False:
        return "disabled"
    return "unknown"


def _status_text(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return ", ".join(f"{key}:{count}" for key, count in sorted(value.items()))
