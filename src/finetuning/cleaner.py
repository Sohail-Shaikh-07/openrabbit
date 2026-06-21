"""Data cleaner for the CodeReviewer Comment_Generation dataset.

Removes noise and duplicates from the raw examples produced by
:class:`~finetuning.dataset.DatasetLoader` so the downstream instruction
formatter receives only high-quality (patch, review comment) pairs.

Filters applied in order:

1. **Empty patch** -- diff hunk is blank or whitespace-only.
2. **Short message** -- review comment is under 20 characters.
3. **Noise phrase** -- exact LGTM-style approvals with no useful content.
4. **No alphabetic content** -- message is purely punctuation/numbers.
5. **Deduplication** -- same (patch, msg) pair seen before (keeps first).

``oldf`` (the full old file content) is intentionally dropped from the output
because it can be tens of thousands of characters and is not needed for the
training prompt format we use in OP-26.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from finetuning.dataset import RawExample

logger = logging.getLogger(__name__)

_MIN_MSG_LEN = 20

# Lowercase prefixes / exact phrases that signal a low-value comment.
# Checked against stripped-lowercase msg.
_NOISE_PREFIXES: tuple[str, ...] = (
    "lgtm",
    "looks good",
    "+1",
    "done",
    "fixed",
    "nice",
    "good job",
    "ok.",
    "ok!",
    "ok,",
    "ok ",
    "nit:",
    "nit ",
    "/lgtm",
    "approved",
    "agreed",
    "sure.",
    "sure!",
    ":+1:",
    "thumbs up",
    "ship it",
    "shipit",
)

_HAS_ALPHA = re.compile(r"[a-zA-Z]")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanExample:
    """A cleaned (patch, review comment) pair ready for instruction formatting.

    ``oldf`` is intentionally excluded -- it is large and not used in training.

    Attributes
    ----------
    patch:
        The diff hunk that was shown to the reviewer.
    msg:
        The review comment. This becomes the model's target output.
    """

    patch: str
    msg: str


@dataclass
class CleaningStats:
    """Counts of examples removed by each filter.

    Attributes
    ----------
    input_count:
        Total raw examples received.
    removed_empty_patch:
        Removed because the patch was empty or whitespace-only.
    removed_short_msg:
        Removed because the message was under 20 characters.
    removed_noise_phrase:
        Removed because the message matched a known LGTM-style phrase.
    removed_no_alpha:
        Removed because the message contained no alphabetic characters.
    removed_duplicate:
        Removed because the (patch, msg) pair was already seen.
    output_count:
        Examples that survived all filters.
    """

    input_count: int = 0
    removed_empty_patch: int = 0
    removed_short_msg: int = 0
    removed_noise_phrase: int = 0
    removed_no_alpha: int = 0
    removed_duplicate: int = 0
    output_count: int = 0


# ---------------------------------------------------------------------------
# DataCleaner
# ---------------------------------------------------------------------------


class DataCleaner:
    """Filters and deduplicates raw examples from the CodeReviewer dataset."""

    def clean(self, examples: Iterable[RawExample]) -> Iterator[CleanExample]:
        """Yield :class:`CleanExample` objects that pass all quality filters.

        Parameters
        ----------
        examples:
            Iterable of :class:`~finetuning.dataset.RawExample` objects,
            typically from :meth:`~finetuning.dataset.DatasetLoader.load`.
        """
        seen: set[str] = set()
        for raw in examples:
            result = self._apply_filters(raw, seen)
            if result is not None:
                yield result

    def clean_with_stats(
        self, examples: Iterable[RawExample]
    ) -> tuple[list[CleanExample], CleaningStats]:
        """Return (cleaned list, stats) for *examples*.

        Reads the full iterable into memory. For the 117K training split this
        uses roughly 200 MB. Use :meth:`clean` when streaming is preferred.
        """
        stats = CleaningStats()
        seen: set[str] = set()
        cleaned: list[CleanExample] = []

        for raw in examples:
            stats.input_count += 1
            patch = raw.patch.strip()
            msg = raw.msg.strip()

            if not patch:
                stats.removed_empty_patch += 1
                continue

            if len(msg) < _MIN_MSG_LEN:
                stats.removed_short_msg += 1
                continue

            if _is_noise(msg):
                stats.removed_noise_phrase += 1
                continue

            if not _HAS_ALPHA.search(msg):
                stats.removed_no_alpha += 1
                continue

            key = _pair_hash(patch, msg)
            if key in seen:
                stats.removed_duplicate += 1
                continue
            seen.add(key)

            cleaned.append(CleanExample(patch=patch, msg=msg))

        stats.output_count = len(cleaned)
        return cleaned, stats

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_filters(self, raw: RawExample, seen: set[str]) -> CleanExample | None:
        """Return a :class:`CleanExample` or ``None`` if the example fails a filter."""
        patch = raw.patch.strip()
        msg = raw.msg.strip()

        if not patch:
            return None
        if len(msg) < _MIN_MSG_LEN:
            return None
        if _is_noise(msg):
            return None
        if not _HAS_ALPHA.search(msg):
            return None

        key = _pair_hash(patch, msg)
        if key in seen:
            return None
        seen.add(key)

        return CleanExample(patch=patch, msg=msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_NOISE_EXACT: frozenset[str] = frozenset(
    {
        "lgtm",
        "+1",
        "done",
        "fixed",
        "nice",
        "ok",
        "agreed",
        "approved",
        ":+1:",
        "ship it",
        "shipit",
        "good job",
        "thumbs up",
        "looks good",
        "looks good to me",
        "looks good!",
        "lgtm!",
        "nit",
        "/lgtm",
    }
)

# Prefix-based matching only fires for short messages (< 60 chars) so that
# "looks good but we should add error handling..." is NOT filtered out.
_SHORT_NOISE_THRESHOLD = 60


def _is_noise(msg: str) -> bool:
    """Return True if *msg* is a low-value review phrase with no substance."""
    lower = msg.lower().strip()
    cleaned = lower.rstrip(".!?,;:")

    # Exact match always fires regardless of length.
    if cleaned in _NOISE_EXACT:
        return True

    # Prefix match only fires on short messages to avoid removing substantive
    # comments that happen to open with "looks good, but..." or "+1, also..."
    if len(msg) < _SHORT_NOISE_THRESHOLD:
        for prefix in _NOISE_PREFIXES:
            if cleaned.startswith(prefix):
                return True

    return False


def _pair_hash(patch: str, msg: str) -> str:
    """Return a short hex digest uniquely identifying a (patch, msg) pair."""
    content = f"{patch}\x00{msg}"
    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()
