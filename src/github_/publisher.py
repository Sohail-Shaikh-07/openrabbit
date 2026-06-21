"""GitHub review publisher for OpenRabbit.

Posts inline review comments and a summary review body to a pull request
using the GitHub REST API. Uses the existing GitHubClient so all auth,
retry, and rate-limit handling is inherited.
"""

from __future__ import annotations

import logging

from github_.client import GitHubClient
from github_.models import ReviewComment
from ranking.ranker import RankedFinding

logger = logging.getLogger(__name__)

_SEVERITY_EMOJI = {
    "low": "🔵",
    "medium": "🟡",
    "high": "🔴",
    "critical": "🚨",
}

_SUMMARY_HEADER = "## OpenRabbit Review\n\n"
_NO_FINDINGS_BODY = "## OpenRabbit Review\n\nNo significant issues found. Looks good! ✅"


class GitHubPublisher:
    """Posts ranked findings as inline PR comments via the GitHub REST API.

    Parameters
    ----------
    token:
        GitHub personal access token.
    owner:
        Repository owner (user or organisation).
    repo:
        Repository name.
    """

    def __init__(self, token: str, owner: str, repo: str) -> None:
        self._token = token
        self._owner = owner
        self._repo = repo

    async def publish(
        self,
        *,
        pr_number: int,
        ranked: list[RankedFinding],
        head_sha: str,
    ) -> None:
        """Post a GitHub pull request review from *ranked* findings.

        If *ranked* is empty, no API call is made. Otherwise one review is
        posted with inline comments for each finding and a markdown summary
        body.
        """
        if not ranked:
            logger.info("No findings to publish for PR #%d", pr_number)
            return

        comments = [_to_review_comment(rf) for rf in ranked]
        body = _build_summary(ranked)

        async with GitHubClient(token=self._token) as client:
            review = await client.create_review(
                self._owner,
                self._repo,
                pr_number,
                body=body,
                event="COMMENT",
                comments=comments,
                commit_id=head_sha,
            )
            logger.info(
                "Posted review %d for PR #%d (%d comments)",
                review.id,
                pr_number,
                len(comments),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_review_comment(rf: RankedFinding) -> ReviewComment:
    f = rf.finding
    emoji = _SEVERITY_EMOJI.get(f.severity.name, "⚠️")
    body = (
        f"{emoji} **[{f.severity.name.upper()}] {f.title}**\n\n"
        f"{f.reason}\n\n"
        f"**Suggestion:** {f.suggestion}"
    )
    if f.fix:
        body += f"\n\n```\n{f.fix}\n```"
    return ReviewComment(
        path=f.file,
        body=body,
        line=f.line if f.line > 0 else None,
        side="RIGHT",
    )


def _build_summary(ranked: list[RankedFinding]) -> str:
    count = len(ranked)
    lines = [
        _SUMMARY_HEADER,
        f"Found **{count}** issue{'s' if count != 1 else ''} across the PR.\n\n",
        "| Severity | File | Line | Title |\n",
        "|---|---|---|---|\n",
    ]
    for rf in ranked:
        f = rf.finding
        emoji = _SEVERITY_EMOJI.get(f.severity.name, "⚠️")
        lines.append(f"| {emoji} {f.severity.name.upper()} | `{f.file}` | {f.line} | {f.title} |\n")
    return "".join(lines)
