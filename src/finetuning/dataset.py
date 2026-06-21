"""Dataset loader for the Zenodo CodeReviewer Comment_Generation dataset.

The Comment_Generation task uses pull request diffs and their corresponding
review comments. We load only the fields we need for fine-tuning:

    patch  -- the diff hunk shown to the reviewer
    msg    -- the review comment (our training target)

The ``oldf`` field (full old file content) is stored for completeness but
not used during training since it can be very large (10K+ chars).

Usage::

    from finetuning.dataset import DatasetLoader

    loader = DatasetLoader()
    for example in loader.load(Path("dataset/Comment_Generation/msg-train.jsonl")):
        print(example.msg)

    stats = loader.stats(Path("dataset/Comment_Generation/msg-train.jsonl"))
    print(f"Total examples: {stats.total}")
    print(f"Median message length: {stats.msg_len_p50} chars")
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SHORT_MSG_THRESHOLD = 20


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawExample:
    """One example from the CodeReviewer Comment_Generation JSONL file.

    Attributes
    ----------
    oldf:
        The original file content before the change. May be very large.
    patch:
        The diff hunk shown to the code reviewer.
    msg:
        The review comment left by the reviewer. This is the training target.
    id:
        Unique integer ID from the original dataset.
    y:
        Quality label. In the Comment_Generation split all examples have y=1.
    """

    oldf: str
    patch: str
    msg: str
    id: int
    y: int


@dataclass(frozen=True)
class DatasetStats:
    """Summary statistics over a JSONL split.

    Attributes
    ----------
    total:
        Total number of valid examples in the file.
    msg_len_p50:
        Median review comment length in characters.
    msg_len_p90:
        90th-percentile review comment length in characters.
    patch_len_p50:
        Median diff hunk length in characters.
    patch_len_p90:
        90th-percentile diff hunk length in characters.
    short_msg_count:
        Number of examples whose message is shorter than 20 characters.
        These are candidates for removal during cleaning.
    """

    total: int
    msg_len_p50: int
    msg_len_p90: int
    patch_len_p50: int
    patch_len_p90: int
    short_msg_count: int


# ---------------------------------------------------------------------------
# DatasetLoader
# ---------------------------------------------------------------------------


class DatasetLoader:
    """Streams examples from a CodeReviewer Comment_Generation JSONL file.

    Streaming avoids loading the full 3.4 GB training file into memory.
    Malformed lines are skipped with a warning so a single corrupt record
    does not abort the whole load.
    """

    def load(self, path: Path) -> Iterator[RawExample]:
        """Yield :class:`RawExample` objects from *path*.

        Parameters
        ----------
        path:
            Path to a ``.jsonl`` file in the CodeReviewer Comment_Generation
            format.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        """
        if not path.exists():
            raise FileNotFoundError(f"Dataset file not found: {path}")

        with path.open(encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                    yield RawExample(
                        oldf=str(record.get("oldf", "")),
                        patch=str(record.get("patch", "")),
                        msg=str(record.get("msg", "")),
                        id=int(record.get("id", 0)),
                        y=int(record.get("y", 1)),
                    )
                except (json.JSONDecodeError, KeyError, ValueError):
                    logger.warning("Skipping malformed record at line %d in %s", lineno, path)

    def stats(self, path: Path) -> DatasetStats:
        """Return summary statistics for the dataset at *path*.

        Reads the entire file once. For the 3.4 GB training split this takes
        roughly 30-60 seconds depending on disk speed.
        """
        msg_lengths: list[int] = []
        patch_lengths: list[int] = []
        short_count = 0

        for ex in self.load(path):
            ml = len(ex.msg)
            pl = len(ex.patch)
            msg_lengths.append(ml)
            patch_lengths.append(pl)
            if ml < _SHORT_MSG_THRESHOLD:
                short_count += 1

        total = len(msg_lengths)
        if total == 0:
            return DatasetStats(
                total=0,
                msg_len_p50=0,
                msg_len_p90=0,
                patch_len_p50=0,
                patch_len_p90=0,
                short_msg_count=0,
            )

        msg_lengths.sort()
        patch_lengths.sort()

        return DatasetStats(
            total=total,
            msg_len_p50=msg_lengths[int(0.50 * total)],
            msg_len_p90=msg_lengths[int(0.90 * total)],
            patch_len_p50=patch_lengths[int(0.50 * total)],
            patch_len_p90=patch_lengths[int(0.90 * total)],
            short_msg_count=short_count,
        )
