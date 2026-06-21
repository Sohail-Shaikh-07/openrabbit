"""Tests for GitHubPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.models import Finding, Severity
from github_.publisher import GitHubPublisher
from ranking.ranker import RankedFinding


def _ranked(
    title: str = "SQL injection",
    file: str = "auth.py",
    line: int = 10,
    severity: Severity = Severity.high,
    confidence: float = 0.90,
    suggestion: str = "Use parameterized queries.",
    reason: str = "Unsanitized input.",
) -> RankedFinding:
    finding = Finding(
        severity=severity,
        category="security",
        file=file,
        line=line,
        confidence=confidence,
        title=title,
        reason=reason,
        suggestion=suggestion,
        fix="",
    )
    return RankedFinding(finding=finding, score=severity * confidence)


# ---------------------------------------------------------------------------
# GitHubPublisher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_calls_create_review() -> None:
    publisher = GitHubPublisher(token="tok", owner="org", repo="repo")
    ranked = [_ranked()]

    with patch("github_.publisher.GitHubClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.create_review = AsyncMock(return_value=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await publisher.publish(pr_number=42, ranked=ranked, head_sha="abc123")

    mock_client.create_review.assert_called_once()


@pytest.mark.asyncio
async def test_publisher_passes_correct_pr_number() -> None:
    publisher = GitHubPublisher(token="tok", owner="org", repo="repo")

    with patch("github_.publisher.GitHubClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.create_review = AsyncMock(return_value=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await publisher.publish(pr_number=99, ranked=[_ranked()], head_sha="sha1")

    call_args = mock_client.create_review.call_args
    # pr_number is the third positional arg: (owner, repo, pr_number, ...)
    assert call_args.args[2] == 99


@pytest.mark.asyncio
async def test_publisher_includes_inline_comment_per_finding() -> None:
    publisher = GitHubPublisher(token="tok", owner="org", repo="repo")
    findings = [_ranked("Issue A", file="a.py", line=1), _ranked("Issue B", file="b.py", line=2)]

    with patch("github_.publisher.GitHubClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.create_review = AsyncMock(return_value=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await publisher.publish(pr_number=1, ranked=findings, head_sha="sha")

    call_kwargs = mock_client.create_review.call_args.kwargs
    comments = call_kwargs.get("comments", [])
    assert len(comments) == 2


@pytest.mark.asyncio
async def test_publisher_skips_if_no_findings() -> None:
    publisher = GitHubPublisher(token="tok", owner="org", repo="repo")

    with patch("github_.publisher.GitHubClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.create_review = AsyncMock(return_value=MagicMock())
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await publisher.publish(pr_number=1, ranked=[], head_sha="sha")

    mock_client.create_review.assert_not_called()


@pytest.mark.asyncio
async def test_publisher_summary_mentions_finding_count() -> None:
    publisher = GitHubPublisher(token="tok", owner="org", repo="repo")
    findings = [_ranked("A"), _ranked("B"), _ranked("C")]

    captured_body: list[str] = []

    async def fake_create_review(*args: object, **kwargs: object) -> MagicMock:
        # body is a keyword arg in our publisher call
        captured_body.append(str(kwargs.get("body", "")))
        return MagicMock()

    with patch("github_.publisher.GitHubClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.create_review = AsyncMock(side_effect=fake_create_review)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await publisher.publish(pr_number=1, ranked=findings, head_sha="sha")

    assert len(captured_body) == 1
    assert "3" in captured_body[0]
