"""Tests for the local model review pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.base import BaseReviewAgent
from agents.models import AgentResult, Finding, ReviewState, Severity
from cli.commands.review_pipeline import run_agent_review


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


@pytest.mark.asyncio
async def test_run_agent_review_returns_ranked_findings() -> None:
    pr_payload = MagicMock()
    pr_payload.diff = "diff --git a/src/app.py b/src/app.py\n+value.name"

    result = await run_agent_review(pr_payload, agents=[StubAgent()])

    assert len(result.agent_results) == 1
    assert len(result.ranked_findings) == 1
    assert result.ranked_findings[0].finding.title == "Missing None guard"
    assert result.ranked_findings[0].score == pytest.approx(2.7)
