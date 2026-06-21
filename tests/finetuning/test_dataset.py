"""Tests for finetuning.dataset.

All tests use tiny in-memory JSONL fixtures so no real dataset file is
needed in CI. The real 3.4 GB msg-train.jsonl only exists locally.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finetuning.dataset import DatasetLoader, DatasetStats, RawExample

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records),
        encoding="utf-8",
    )


def _sample_records(n: int = 5) -> list[dict]:
    return [
        {
            "oldf": f"def old_func_{i}(): pass",
            "patch": f"@@ -1 +1 @@\n-def old_func_{i}(): pass\n+def new_func_{i}(): pass",
            "msg": f"Rename old_func_{i} to something more descriptive.",
            "id": 1000 + i,
            "y": 1,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# RawExample
# ---------------------------------------------------------------------------


def test_raw_example_stores_all_fields() -> None:
    ex = RawExample(
        oldf="def foo(): pass",
        patch="@@ -1 +1 @@",
        msg="Consider renaming foo.",
        id=42,
        y=1,
    )
    assert ex.oldf == "def foo(): pass"
    assert ex.patch == "@@ -1 +1 @@"
    assert ex.msg == "Consider renaming foo."
    assert ex.id == 42
    assert ex.y == 1


def test_raw_example_is_frozen() -> None:
    ex = RawExample(oldf="x", patch="y", msg="z", id=1, y=1)
    with pytest.raises((AttributeError, TypeError)):
        ex.msg = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DatasetLoader.load
# ---------------------------------------------------------------------------


def test_load_returns_raw_examples(tmp_path: Path) -> None:
    records = _sample_records(5)
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    examples = list(DatasetLoader().load(jsonl))

    assert len(examples) == 5
    assert all(isinstance(e, RawExample) for e in examples)


def test_load_maps_fields_correctly(tmp_path: Path) -> None:
    records = [{"oldf": "old", "patch": "@@ -1 +1 @@", "msg": "Nice catch.", "id": 7, "y": 1}]
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    ex = next(iter(DatasetLoader().load(jsonl)))

    assert ex.oldf == "old"
    assert ex.patch == "@@ -1 +1 @@"
    assert ex.msg == "Nice catch."
    assert ex.id == 7
    assert ex.y == 1


def test_load_raises_for_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        list(DatasetLoader().load(Path("/nonexistent/file.jsonl")))


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    jsonl = tmp_path / "train.jsonl"
    jsonl.write_text(
        '{"oldf":"a","patch":"b","msg":"c","id":1,"y":1}\n'
        "NOT_VALID_JSON\n"
        '{"oldf":"d","patch":"e","msg":"f","id":2,"y":1}\n',
        encoding="utf-8",
    )

    examples = list(DatasetLoader().load(jsonl))
    assert len(examples) == 2


def test_load_handles_empty_file(tmp_path: Path) -> None:
    jsonl = tmp_path / "empty.jsonl"
    jsonl.write_text("", encoding="utf-8")

    examples = list(DatasetLoader().load(jsonl))
    assert examples == []


def test_load_streams_without_loading_all_at_once(tmp_path: Path) -> None:
    records = _sample_records(100)
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    # Should be a generator, not a list -- check we can break early
    loader = DatasetLoader()
    gen = loader.load(jsonl)
    first = next(gen)
    assert isinstance(first, RawExample)


# ---------------------------------------------------------------------------
# DatasetLoader.stats
# ---------------------------------------------------------------------------


def test_stats_returns_dataset_stats(tmp_path: Path) -> None:
    records = _sample_records(10)
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    stats = DatasetLoader().stats(jsonl)

    assert isinstance(stats, DatasetStats)


def test_stats_counts_total_examples(tmp_path: Path) -> None:
    records = _sample_records(12)
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    stats = DatasetLoader().stats(jsonl)

    assert stats.total == 12


def test_stats_computes_msg_length_percentiles(tmp_path: Path) -> None:
    records = [
        {"oldf": "", "patch": "", "msg": "x" * n, "id": i, "y": 1}
        for i, n in enumerate([10, 50, 100, 200, 300])
    ]
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    stats = DatasetLoader().stats(jsonl)

    assert stats.msg_len_p50 >= 10
    assert stats.msg_len_p90 >= stats.msg_len_p50


def test_stats_computes_patch_length_percentiles(tmp_path: Path) -> None:
    records = [
        {"oldf": "", "patch": "p" * n, "msg": "review", "id": i, "y": 1}
        for i, n in enumerate([100, 200, 400, 800, 1600])
    ]
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    stats = DatasetLoader().stats(jsonl)

    assert stats.patch_len_p50 >= 100
    assert stats.patch_len_p90 >= stats.patch_len_p50


def test_stats_short_msg_count(tmp_path: Path) -> None:
    records = [
        {"oldf": "", "patch": "", "msg": msg, "id": i, "y": 1}
        for i, msg in enumerate(["ok", "LGTM", "A" * 50, "B" * 100])
    ]
    jsonl = tmp_path / "train.jsonl"
    _write_jsonl(jsonl, records)

    stats = DatasetLoader().stats(jsonl)

    # 2 messages are under 20 chars ("ok"=2, "LGTM"=4)
    assert stats.short_msg_count == 2
