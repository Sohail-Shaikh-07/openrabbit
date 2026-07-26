"""Read-only pull request label proposal command."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, TextIO

from cli.commands.history import load_pr_history
from cli.commands.output import render_json
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import GitHubClient, PullRequestParser, RepositoryHandle

_log = get_logger(__name__)

_WORD_BOUNDARY = r"(?:^|[^a-z0-9])"
_LABEL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bug", ("bug", "fix", "defect", "error", "crash", "regression", "broken")),
    (
        "enhancement",
        ("add", "added", "feature", "implement", "support", "enable", "new"),
    ),
    ("documentation", ("doc", "docs", "readme", "guide", "documentation")),
    ("tests", ("test", "tests", "pytest", "coverage", "spec", "regression test")),
    (
        "security",
        ("auth", "admin", "token", "secret", "permission", "security", "csrf", "xss"),
    ),
    ("performance", ("perf", "performance", "cache", "latency", "slow", "optimize")),
    ("dependencies", ("dependency", "dependencies", "upgrade", "bump", "lockfile")),
    ("refactor", ("refactor", "cleanup", "simplify", "restructure")),
)

_PATH_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("documentation", ("README", "docs/", ".md", ".rst")),
    ("tests", ("tests/", "test_", "_test.", ".spec.", ".test.")),
    ("cli", ("src/cli/", "cli/", "commands/")),
    ("github-integration", ("src/github_/", ".github/", "github")),
    ("infrastructure", ("Dockerfile", "docker-compose", ".github/", "pyproject.toml")),
    ("config", ("config", ".yml", ".yaml", ".toml", ".env")),
    ("agents", ("src/agents/", "agent", "langgraph")),
    ("rag", ("src/rag/", "qdrant", "retriever", "chunk")),
    ("repository-context", ("src/knowledge/", "context", "connector")),
)

_FINDING_CATEGORY_LABELS = {
    "security": "security",
    "performance": "performance",
    "tests": "tests",
    "test": "tests",
    "bug": "bug",
    "architecture": "architecture",
    "style": "refactor",
}


@dataclass
class _Candidate:
    """Internal label candidate with accumulated evidence."""

    name: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    def add(self, points: float, reason: str, signal: str) -> None:
        self.score += points
        if reason not in self.reasons:
            self.reasons.append(reason)
        if signal not in self.signals:
            self.signals.append(signal)


@dataclass(frozen=True)
class LabelProposal:
    """One read-only label suggestion with evidence."""

    name: str
    confidence: float
    reason: str
    signals: list[str]
    exists_in_repository: bool | None = None


async def run_label_proposals(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    limit: int = 8,
) -> dict[str, object]:
    """Fetch a PR and return deterministic, read-only label proposals."""
    if limit < 1:
        raise ValueError("--limit must be at least 1")

    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    try:
        handle = RepositoryHandle.from_full_name(target, client)
        payload = await PullRequestParser(handle).parse(number)
        repo_labels, labels_loaded = await _load_repository_labels(handle)
        pr_history_result = await load_pr_history(
            settings,
            handle=handle,
            payload=payload,
            include_conversation=False,
        )
    finally:
        await client.aclose()

    current_labels = _current_label_names(payload)
    proposals = _propose_labels(
        payload,
        current_labels=current_labels,
        repository_labels=repo_labels,
        history=pr_history_result.history,
        limit=limit,
    )
    hunk_total = sum(len(f.hunks) for f in payload.files)
    binary_count = sum(1 for f in payload.files if f.is_binary)
    return {
        "schema_version": "1.0",
        "command": "labels",
        "repo": handle.full_name,
        "number": payload.number,
        "title": payload.pull_request.title,
        "state": payload.pull_request.state,
        "head_sha": payload.head_sha[:12],
        "files_changed": len(payload.files),
        "binary_files": binary_count,
        "hunks": hunk_total,
        "commits": len(payload.commits),
        "current_labels": current_labels,
        "repository_labels_loaded": labels_loaded,
        "repository_labels_count": len(repo_labels),
        "linked_issue_count": len(payload.linked_issues),
        "memory_enabled": pr_history_result.history is not None,
        "learning_count": pr_history_result.learning_count,
        "conversation_count": pr_history_result.conversation_count,
        "proposal_count": len(proposals),
        "label_proposals": [_serialize_proposal(proposal) for proposal in proposals],
        "mutates_github": False,
    }


def run_label_proposals_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    limit: int = 8,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(
        run_label_proposals(
            settings,
            number=number,
            repo=repo,
            env=env,
            limit=limit,
        )
    )


def render_label_proposals(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_label_proposals`."""
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
    print(f"  Existing:     {_format_labels(summary.get('current_labels'))}", file=out)
    print(f"  Proposals:    {summary['proposal_count']}", file=out)
    print("  GitHub write: no", file=out)

    raw_proposals = summary.get("label_proposals")
    proposals = raw_proposals if isinstance(raw_proposals, list) else []
    if not proposals:
        return

    print("", file=out)
    print("Label proposals:", file=out)
    for item in proposals:
        if not isinstance(item, dict):
            continue
        confidence = item.get("confidence", 0)
        confidence_text = f"{confidence:.2f}" if isinstance(confidence, int | float) else ""
        exists = item.get("exists_in_repository")
        exists_text = ""
        if exists is False:
            exists_text = " (not in repository labels)"
        print(f"  - {item.get('name', '')} ({confidence_text}){exists_text}", file=out)
        print(f"    {item.get('reason', '')}", file=out)
        signals = item.get("signals")
        if isinstance(signals, list) and signals:
            print(f"    Signals: {', '.join(str(signal) for signal in signals[:4])}", file=out)


def render_label_proposals_json(summary: dict[str, object], out: TextIO) -> None:
    """Render label proposals as deterministic JSON."""
    render_json(summary, out)


async def _load_repository_labels(handle: RepositoryHandle) -> tuple[list[str], bool]:
    try:
        labels = await handle.list_labels()
    except Exception as exc:
        _log.warning(
            "labels.repository_labels_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return [], False
    return sorted({label.name for label in labels}, key=str.lower), True


def _propose_labels(
    pr_payload: Any,
    *,
    current_labels: list[str],
    repository_labels: list[str],
    history: Any | None,
    limit: int,
) -> list[LabelProposal]:
    candidates: dict[str, _Candidate] = {}
    current_lookup = {_normalize_label(label) for label in current_labels}
    repo_lookup = {_normalize_label(label): label for label in repository_labels}

    def add(name: str, points: float, reason: str, signal: str) -> None:
        normalized = _normalize_label(name)
        if not normalized or normalized in current_lookup:
            return
        display_name = repo_lookup.get(normalized, name)
        candidate = candidates.setdefault(normalized, _Candidate(name=display_name))
        candidate.add(points, reason, signal)

    text = _metadata_text(pr_payload)
    for label, keywords in _LABEL_RULES:
        matched = _matched_keywords(text, keywords)
        if matched:
            add(
                label,
                0.24 + min(len(matched), 3) * 0.08,
                f"PR metadata mentions {', '.join(matched[:3])}.",
                "metadata",
            )

    for path in _changed_paths(pr_payload):
        for label, fragments in _PATH_RULES:
            if _path_matches(path, fragments):
                add(label, 0.28, f"Changed files touch {label} areas.", f"path:{path}")

    for issue in getattr(pr_payload, "linked_issues", []):
        for label in getattr(issue, "labels", []):
            add(
                str(label),
                0.46,
                f"Linked issue {getattr(issue, 'full_name', '')} has this label.",
                "linked_issue",
            )
        issue_text = " ".join(
            (
                str(getattr(issue, "title", "")),
                str(getattr(issue, "body_preview", "")),
            )
        ).lower()
        for label, keywords in _LABEL_RULES:
            matched = _matched_keywords(issue_text, keywords)
            if matched:
                add(
                    label,
                    0.22,
                    f"Linked issue text mentions {', '.join(matched[:3])}.",
                    "linked_issue_text",
                )

    local = getattr(history, "local", None)
    previous_findings = getattr(local, "previous_findings", []) if local is not None else []
    for finding in previous_findings[:20]:
        category = str(getattr(finding, "category", "")).lower()
        memory_label = _FINDING_CATEGORY_LABELS.get(category)
        if memory_label:
            add(
                memory_label,
                0.34,
                f"Previous OpenRabbit review context includes {category} findings.",
                "review_memory",
            )
        severity = str(getattr(finding, "severity", "")).lower()
        if severity in {"critical", "high"}:
            add(
                "bug",
                0.18,
                "Previous OpenRabbit review context includes high-severity findings.",
                "review_memory",
            )

    proposals = [
        _to_proposal(candidate, repo_lookup=repo_lookup)
        for candidate in candidates.values()
        if candidate.score >= 0.32
    ]
    return sorted(
        proposals,
        key=lambda proposal: (-proposal.confidence, proposal.name.lower()),
    )[:limit]


def _to_proposal(
    candidate: _Candidate,
    *,
    repo_lookup: dict[str, str],
) -> LabelProposal:
    exists: bool | None = None
    if repo_lookup:
        exists = _normalize_label(candidate.name) in repo_lookup
    return LabelProposal(
        name=candidate.name,
        confidence=round(min(candidate.score, 0.99), 2),
        reason=" ".join(candidate.reasons[:3]),
        signals=candidate.signals[:6],
        exists_in_repository=exists,
    )


def _metadata_text(pr_payload: Any) -> str:
    pr = pr_payload.pull_request
    commits = getattr(pr_payload, "commits", [])
    commit_messages = [
        str(getattr(getattr(commit, "commit", None), "message", "")) for commit in commits
    ]
    return " ".join(
        (
            str(getattr(pr, "title", "")),
            str(getattr(pr, "body", "") or ""),
            str(getattr(getattr(pr, "head", None), "ref", "")),
            " ".join(commit_messages),
        )
    ).lower()


def _current_label_names(pr_payload: Any) -> list[str]:
    labels = getattr(pr_payload.pull_request, "labels", [])
    names = {str(getattr(label, "name", "")) for label in labels}
    return sorted({name for name in names if name}, key=str.lower)


def _changed_paths(pr_payload: Any) -> list[str]:
    return [
        str(getattr(file_, "path", "")) for file_ in pr_payload.files if getattr(file_, "path", "")
    ]


def _matched_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for keyword in keywords:
        pattern = rf"{_WORD_BOUNDARY}{re.escape(keyword.lower())}(?:$|[^a-z0-9])"
        if re.search(pattern, text):
            matches.append(keyword)
    return matches


def _path_matches(path: str, fragments: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").lower()
    return any(fragment.lower() in normalized for fragment in fragments)


def _normalize_label(label: str) -> str:
    return " ".join(label.strip().lower().split())


def _serialize_proposal(proposal: LabelProposal) -> dict[str, object]:
    return {
        "name": proposal.name,
        "confidence": proposal.confidence,
        "reason": proposal.reason,
        "signals": proposal.signals,
        "exists_in_repository": proposal.exists_in_repository,
    }


def _format_labels(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    labels = [str(label) for label in value if str(label).strip()]
    return ", ".join(labels) if labels else "none"
