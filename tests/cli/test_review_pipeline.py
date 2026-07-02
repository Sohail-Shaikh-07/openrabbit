"""Tests for the local model review pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.base import BaseReviewAgent
from agents.models import AgentResult, Finding, ReviewState, Severity
from cli.commands.review_pipeline import run_agent_review
from github_.diff import DiffLine, Hunk


class StubAgent(BaseReviewAgent):
    name = "stub"

    async def run(self, state: ReviewState) -> AgentResult:
        return AgentResult(
            agent=self.name,
            findings=[
                Finding(
                    severity=Severity.high,
                    category="bug",
                    file="src/app.py",
                    line=12,
                    confidence=0.9,
                    title="Missing None guard",
                    reason="The value can be None.",
                    suggestion="Add a guard clause.",
                    fix="if value is None: return",
                )
            ],
            confidence=0.9,
            execution_time=0.01,
        )


class UngroundedStubAgent(BaseReviewAgent):
    name = "ungrounded_stub"

    async def run(self, state: ReviewState) -> AgentResult:
        return AgentResult(
            agent=self.name,
            findings=[
                Finding(
                    severity=Severity.high,
                    category="security",
                    file="src/search.py",
                    line=11,
                    confidence=0.9,
                    title="Unsafe raw SQL",
                    reason="User input reaches raw SQL.",
                    suggestion="Use parameterized queries.",
                    fix="",
                ),
                Finding(
                    severity=Severity.high,
                    category="tests",
                    file="tests/test_fake.py",
                    line=10,
                    confidence=0.9,
                    title="Missing unit test",
                    reason="The model invented this file.",
                    suggestion="Do not show this.",
                    fix="",
                ),
            ],
            confidence=0.9,
            execution_time=0.01,
        )


@pytest.mark.asyncio
async def test_run_agent_review_returns_ranked_findings() -> None:
    pr_payload = MagicMock()
    pr_payload.diff = "diff --git a/src/app.py b/src/app.py\n+value.name"

    result = await run_agent_review(pr_payload, agents=[StubAgent()])

    assert len(result.agent_results) == 1
    assert len(result.ranked_findings) == 1
    assert result.ranked_findings[0].finding.title == "Missing None guard"
    assert result.ranked_findings[0].score == pytest.approx(2.7)


@pytest.mark.asyncio
async def test_run_agent_review_filters_ungrounded_findings_before_ranking() -> None:
    pr_payload = MagicMock()
    pr_payload.files = [
        MagicMock(
            path="src/search.py",
            hunks=[
                Hunk(
                    old_start=10,
                    old_lines=1,
                    new_start=10,
                    new_lines=2,
                    lines=[
                        DiffLine(kind="context", text="def search_tasks(q):"),
                        DiffLine(kind="addition", text="    return db.execute(q)"),
                    ],
                )
            ],
        )
    ]

    result = await run_agent_review(pr_payload, agents=[UngroundedStubAgent()])

    assert [rf.finding.title for rf in result.ranked_findings] == ["Unsafe raw SQL"]
    assert result.dropped_findings_count == 1
