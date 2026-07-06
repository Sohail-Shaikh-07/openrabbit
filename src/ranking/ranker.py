"""Comment ranker for OpenRabbit.

Collects all findings from every review agent, deduplicates them by
(file, line, normalised title) or nearby semantic root cause, scores each by severity * confidence,
and returns the top-N results ordered by score descending.

This is the last step before the GitHub publisher posts comments.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.models import AgentResult, Finding

_DEFAULT_TOP_N = 10
_SEMANTIC_LINE_WINDOW = 5


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

    Exact duplicates share the same (file, line, normalised title). Semantic
    duplicates point to nearby lines in the same file and describe the same
    root cause. In both cases, the higher-scored finding is kept.
    """
    deduped: list[Finding] = []
    for finding in findings:
        duplicate_index = _find_duplicate_index(deduped, finding)
        if duplicate_index is None:
            deduped.append(finding)
            continue
        if _raw_score(finding) > _raw_score(deduped[duplicate_index]):
            deduped[duplicate_index] = finding
    return deduped


def _find_duplicate_index(existing: list[Finding], candidate: Finding) -> int | None:
    for index, finding in enumerate(existing):
        if _same_exact_finding(finding, candidate) or _same_semantic_finding(finding, candidate):
            return index
    return None


def _same_exact_finding(a: Finding, b: Finding) -> bool:
    return a.file == b.file and a.line == b.line and _normalise(a.title) == _normalise(b.title)


def _same_semantic_finding(a: Finding, b: Finding) -> bool:
    if a.file != b.file:
        return False
    if abs(a.line - b.line) > _SEMANTIC_LINE_WINDOW:
        return False

    a_kind = _issue_kind(a)
    return a_kind != "" and a_kind == _issue_kind(b)


def _normalise(title: str) -> str:
    return title.lower().strip()


def _issue_kind(finding: Finding) -> str:
    text = " ".join((finding.category, finding.title, finding.reason, finding.suggestion)).lower()
    if "sql" in text or "injection" in text or "raw query" in text or "raw sql" in text:
        return "sql_injection"
    if _contains_any(text, ("null", "none", "nil")) and _contains_any(
        text,
        ("dereference", "attribute", "access"),
    ):
        return "null_dereference"
    if _contains_any(text, ("authorization", "permission", "privilege", "admin")):
        return "authorization"
    return ""


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _raw_score(f: Finding) -> float:
    return int(f.severity) * f.confidence


def _score(f: Finding) -> RankedFinding:
    return RankedFinding(finding=f, score=_raw_score(f))
