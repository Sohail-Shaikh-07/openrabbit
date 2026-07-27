"""Read-only similar issue lookup command."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, TextIO

from cli.commands.history import load_pr_history
from cli.commands.maintenance_controls import (
    read_only_workflow_controls,
    render_workflow_control_lines,
)
from cli.commands.output import render_json
from cli.commands.start import resolve_target_repo
from cli.logging import get_logger
from configs.settings import Settings
from github_ import (
    GitHubAPIError,
    GitHubClient,
    Issue,
    LinkedIssue,
    PullRequestParser,
    PullRequestPayload,
    RepositoryHandle,
)
from knowledge.connectors import sanitize_knowledge_text

_log = get_logger(__name__)

_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "before",
    "change",
    "changes",
    "from",
    "into",
    "issue",
    "pull",
    "request",
    "should",
    "that",
    "this",
    "with",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_ISSUE_REF_RE = re.compile(r"(?:[\w.-]+/[\w.-]+)?#\d+|https://github\.com/\S+", re.I)
_CATEGORY_LABELS = {
    "security": "security",
    "performance": "performance",
    "tests": "tests",
    "test": "tests",
    "bug": "bug",
    "architecture": "architecture",
    "style": "refactor",
}


@dataclass
class _IssueCandidate:
    """Internal ranked issue candidate."""

    number: int
    title: str
    state: str
    labels: list[str]
    url: str
    body_preview: str
    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def add(self, points: float, signal: str, reason: str) -> None:
        self.score += points
        if signal not in self.signals:
            self.signals.append(signal)
        if reason not in self.reasons:
            self.reasons.append(reason)


async def run_similar_issues(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    limit: int = 8,
    search_limit: int = 20,
) -> dict[str, object]:
    """Fetch a PR and return similar GitHub issues without mutating anything."""
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    if search_limit < limit:
        raise ValueError("--search-limit must be greater than or equal to --limit")

    target = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    search_loaded = True
    search_error = ""
    try:
        handle = RepositoryHandle.from_full_name(target, client)
        payload = await PullRequestParser(handle).parse(number)
        history_result = await load_pr_history(
            settings,
            handle=handle,
            payload=payload,
            include_conversation=False,
        )
        search_query = _search_query(payload)
        try:
            search_issues = await handle.search_issues(search_query, per_page=search_limit)
        except GitHubAPIError as exc:
            search_issues = []
            search_loaded = False
            search_error = str(exc)
            _log.warning(
                "similar_issues.search_failed",
                repo=handle.full_name,
                pr=payload.number,
                status=exc.status_code,
            )

        results = _rank_issues(
            payload,
            search_issues=search_issues,
            memory_categories=_memory_categories(history_result.history),
            limit=limit,
        )
        return {
            "schema_version": "1.0",
            "command": "similar-issues",
            "repo": handle.full_name,
            "number": payload.number,
            "title": payload.pull_request.title,
            "state": payload.pull_request.state,
            "head_sha": payload.head_sha[:12],
            "files_changed": len(payload.files),
            "linked_issue_count": len(payload.linked_issues),
            "memory_enabled": history_result.history is not None,
            "learning_count": history_result.learning_count,
            "search_query": search_query,
            "search_results_loaded": search_loaded,
            "search_error": search_error,
            "result_count": len(results),
            "issue_results": [_serialize_candidate(candidate) for candidate in results],
            "workflow_controls": read_only_workflow_controls(
                required_permissions=("pull_requests:read", "issues:read"),
            ),
            "mutates_files": False,
            "mutates_github": False,
        }
    finally:
        await client.aclose()


def run_similar_issues_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    limit: int = 8,
    search_limit: int = 20,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(
        run_similar_issues(
            settings,
            number=number,
            repo=repo,
            env=env,
            limit=limit,
            search_limit=search_limit,
        )
    )


def render_similar_issues(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_similar_issues`."""
    print(f"Similar issues for PR #{summary['number']} on {summary['repo']}", file=out)
    print(f"Title:        {summary['title']}", file=out)
    print(f"Linked:       {summary['linked_issue_count']}", file=out)
    print(f"Results:      {summary['result_count']}", file=out)
    if not render_workflow_control_lines(summary, out):
        print("GitHub write: no", file=out)
    if summary.get("search_results_loaded") is False:
        print(f"Search:       failed ({summary.get('search_error', '')})", file=out)
    else:
        print(f"Search:       {summary.get('search_query', '')}", file=out)

    raw_results = summary.get("issue_results")
    results = raw_results if isinstance(raw_results, list) else []
    if not results:
        print("", file=out)
        print("No similar issues matched the pull request signals.", file=out)
        return

    print("", file=out)
    print("Similar issues:", file=out)
    for item in results:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels")
        label_text = f" [{', '.join(labels)}]" if isinstance(labels, list) and labels else ""
        score = _format_score(item.get("score", 0))
        print(
            f"- #{item.get('number', '?')} {item.get('title', 'Untitled')} "
            f"({item.get('state', 'unknown')}, {score}){label_text}",
            file=out,
        )
        signals = item.get("source_signals")
        if isinstance(signals, list) and signals:
            print(f"  Signals: {', '.join(str(signal) for signal in signals[:5])}", file=out)
        reason = str(item.get("reason") or "").strip()
        if reason:
            print(f"  Reason: {reason}", file=out)


def render_similar_issues_json(summary: dict[str, object], out: TextIO) -> None:
    """Render similar issue results as deterministic JSON."""
    render_json(summary, out)


def _format_score(value: object) -> str:
    if not isinstance(value, int | float | str):
        score = 0.0
    else:
        try:
            score = float(value)
        except ValueError:
            score = 0.0
    return f"{score:.2f}"


def _rank_issues(
    payload: PullRequestPayload,
    *,
    search_issues: list[Issue],
    memory_categories: set[str],
    limit: int,
) -> list[_IssueCandidate]:
    pr_tokens = _payload_tokens(payload)
    path_terms = _changed_path_terms(payload)
    pr_labels = _current_labels(payload)
    candidates: dict[int, _IssueCandidate] = {}

    for linked in payload.linked_issues:
        candidate = _candidate_from_linked_issue(linked)
        candidate.add(1.0, "linked_issue", f"PR explicitly references issue #{linked.number}.")
        candidates[linked.number] = candidate

    for issue in search_issues:
        candidate = candidates.get(issue.number) or _candidate_from_issue(issue)
        candidates[issue.number] = candidate
        candidate.add(0.25, "github_issue_search", "Returned by GitHub issue search.")

        issue_tokens = _issue_tokens(candidate)
        overlap = sorted(pr_tokens.intersection(issue_tokens))
        if overlap:
            points = min(0.4, 0.08 * len(overlap))
            candidate.add(points, "metadata_overlap", f"Shared terms: {', '.join(overlap[:6])}.")

        label_overlap = sorted(set(candidate.labels).intersection(pr_labels))
        if label_overlap:
            candidate.add(0.18, "label_overlap", f"Shared labels: {', '.join(label_overlap)}.")

        path_overlap = sorted(path_terms.intersection(issue_tokens))
        if path_overlap:
            points = min(0.2, 0.05 * len(path_overlap))
            candidate.add(
                points, "path_overlap", f"Changed path terms: {', '.join(path_overlap[:5])}."
            )

        memory_overlap = sorted(set(candidate.labels).intersection(memory_categories))
        if memory_overlap:
            candidate.add(
                0.16,
                "memory_category",
                f"Local review memory includes related categories: {', '.join(memory_overlap)}.",
            )

    return sorted(
        candidates.values(),
        key=lambda item: (-item.score, item.number),
    )[:limit]


def _candidate_from_issue(issue: Issue) -> _IssueCandidate:
    return _IssueCandidate(
        number=issue.number,
        title=sanitize_knowledge_text(issue.title, max_chars=180),
        state=issue.state,
        labels=sorted({label.name for label in issue.labels}, key=str.lower),
        url=issue.html_url,
        body_preview=sanitize_knowledge_text(issue.body or "", max_chars=300),
    )


def _candidate_from_linked_issue(issue: LinkedIssue) -> _IssueCandidate:
    return _IssueCandidate(
        number=issue.number,
        title=sanitize_knowledge_text(issue.title, max_chars=180),
        state=issue.state,
        labels=sorted(set(issue.labels), key=str.lower),
        url=issue.url,
        body_preview=sanitize_knowledge_text(issue.body_preview, max_chars=300),
    )


def _search_query(payload: PullRequestPayload) -> str:
    terms = list(_ordered_tokens(_payload_text(payload)))[:8]
    if not terms:
        terms = ["review"]
    return f"{' '.join(terms)} in:title,body"


def _payload_tokens(payload: PullRequestPayload) -> set[str]:
    return set(_ordered_tokens(_payload_text(payload)))


def _payload_text(payload: PullRequestPayload) -> str:
    parts = [
        payload.pull_request.title,
        payload.pull_request.body or "",
        payload.pull_request.head.ref,
    ]
    parts.extend(commit.commit.message for commit in payload.commits)
    parts.extend(issue.title for issue in payload.linked_issues)
    parts.extend(issue.body_preview for issue in payload.linked_issues)
    return _ISSUE_REF_RE.sub(" ", " ".join(parts))


def _issue_tokens(candidate: _IssueCandidate) -> set[str]:
    return set(
        _ordered_tokens(" ".join([candidate.title, candidate.body_preview, *candidate.labels]))
    )


def _ordered_tokens(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group(0).replace("_", "-")
        if token in _STOPWORDS:
            continue
        seen.setdefault(token, None)
    return list(seen)


def _changed_path_terms(payload: PullRequestPayload) -> set[str]:
    terms: set[str] = set()
    for file_ in payload.files:
        path = file_.path.replace("\\", "/").lower()
        for token in re.split(r"[^a-z0-9]+", path):
            if len(token) >= 3 and token not in _STOPWORDS:
                terms.add(token)
    return terms


def _current_labels(payload: PullRequestPayload) -> set[str]:
    labels = getattr(payload.pull_request, "labels", [])
    return {label.name for label in labels if label.name}


def _memory_categories(history: Any | None) -> set[str]:
    if history is None or history.local is None:
        return set()
    labels: set[str] = set()
    for finding in history.local.previous_findings:
        category = str(getattr(finding, "category", "") or "").lower()
        if mapped := _CATEGORY_LABELS.get(category):
            labels.add(mapped)
    return labels


def _serialize_candidate(candidate: _IssueCandidate) -> dict[str, object]:
    return {
        "number": candidate.number,
        "title": candidate.title,
        "state": candidate.state,
        "labels": candidate.labels,
        "url": candidate.url,
        "score": round(min(candidate.score, 1.0), 3),
        "reason": " ".join(candidate.reasons),
        "source_signals": candidate.signals,
        "body_preview": candidate.body_preview,
    }
