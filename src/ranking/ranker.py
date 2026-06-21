"""Comment ranker for OpenRabbit.

Collects all findings from every review agent, deduplicates them by
(file, line, normalised title), scores each by severity * confidence,
and returns the top-N results ordered by score descending.

This is the last step before the GitHub publisher posts comments.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.models import AgentResult, Finding

_DEFAULT_TOP_N = 10


@dataclass
class RankedFinding:
    """A :class:`~agents.models.Finding` with a computed priority score.

    Attributes
    ----------
    finding:
        The original finding produced by a review agent.
    score:
        Priority score in the range ``[0, 4]`` (severity level 1-4 * confidence
        0-1). Higher is more important.
    """

    finding: Finding
    score: float


class CommentRanker:
    """Deduplicates and ranks findings from all review agents.

    Parameters
    ----------
    top_n:
        Maximum number of findings to return. Defaults to 10.
    """

    def __init__(self, top_n: int = _DEFAULT_TOP_N) -> None:
        self._top_n = top_n

    def rank(self, results: list[AgentResult]) -> list[RankedFinding]:
        """Return the top-N findings from *results*, deduped and ranked.

        Two findings are considered duplicates when they share the same
        (file, line, normalised title). The higher-scored duplicate is kept.
        """
        all_findings: list[Finding] = [f for r in results for f in r.findings]
        deduped = _dedup(all_findings)
        ranked = sorted(
            (_score(f) for f in deduped),
            key=lambda rf: rf.score,
            reverse=True,
        )
        return ranked[: self._top_n]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dedup(findings: list[Finding]) -> list[Finding]:
    """Return findings with duplicates removed.

    When two findings share the same (file, line, normalised title), the one
    with the higher score is kept.
    """
    seen: dict[tuple[str, int, str], Finding] = {}
    for f in findings:
        key = (f.file, f.line, _normalise(f.title))
        existing = seen.get(key)
        if existing is None or _raw_score(f) > _raw_score(existing):
            seen[key] = f
    return list(seen.values())


def _normalise(title: str) -> str:
    return title.lower().strip()


def _raw_score(f: Finding) -> float:
    return int(f.severity) * f.confidence


def _score(f: Finding) -> RankedFinding:
    return RankedFinding(finding=f, score=_raw_score(f))
