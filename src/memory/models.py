"""Typed models for local PR memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from agents.models import Finding


class FindingStatus(StrEnum):
    """How a finding relates to previous OpenRabbit reviews."""

    NEW = "new"
    STILL_PRESENT = "still_present"
    POSSIBLY_FIXED = "possibly_fixed"
    STALE = "stale"


@dataclass(frozen=True)
class FindingMemoryRecord:
    """A finding stored in local memory."""

    fingerprint: str
    status: FindingStatus
    title: str
    category: str
    severity: str
    file: str
    line: int
    reason: str
    suggestion: str
    first_seen_sha: str
    last_seen_sha: str
    first_seen_at: datetime
    last_seen_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FindingComparison:
    """Current and resolved findings after comparing a review to memory."""

    current: list[FindingMemoryRecord]
    resolved: list[FindingMemoryRecord]


@dataclass(frozen=True)
class PullRequestMemoryHistory:
    """Local memory known for one pull request."""

    repo: str
    pr_number: int
    last_reviewed_sha: str | None = None
    previous_findings: list[FindingMemoryRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewMemoryWrite:
    """Result of writing one review run into memory."""

    review_id: int
    comparison: FindingComparison


def finding_payload(finding: Finding) -> dict[str, Any]:
    """Return a JSON-safe payload for a finding."""
    return finding.as_dict()
