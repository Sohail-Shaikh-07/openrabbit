"""Tests for finetuning.cleaner.

Each test targets one filter or the dedup logic independently so failures
are easy to diagnose.
"""

from __future__ import annotations

import pytest

from finetuning.cleaner import CleanExample, CleaningStats, DataCleaner
from finetuning.dataset import RawExample

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw(
    patch: str = "@@ -1 +1 @@\n-old\n+new",
    msg: str = "Consider renaming this to something clearer.",
    oldf: str = "old content",
    id: int = 1,
    y: int = 1,
) -> RawExample:
    return RawExample(oldf=oldf, patch=patch, msg=msg, id=id, y=y)


def _clean_all(examples: list[RawExample]) -> list[CleanExample]:
    return list(DataCleaner().clean(examples))


# ---------------------------------------------------------------------------
# CleanExample
# ---------------------------------------------------------------------------


def test_clean_example_has_patch_and_msg() -> None:
    ex = CleanExample(patch="@@ -1 +1 @@", msg="Review comment.")
    assert ex.patch == "@@ -1 +1 @@"
    assert ex.msg == "Review comment."


def test_clean_example_does_not_have_oldf() -> None:
    ex = CleanExample(patch="p", msg="m")
    assert not hasattr(ex, "oldf")


def test_clean_example_is_frozen() -> None:
    ex = CleanExample(patch="p", msg="m")
    with pytest.raises((AttributeError, TypeError)):
        ex.msg = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CleaningStats
# ---------------------------------------------------------------------------


def test_cleaning_stats_tracks_counts() -> None:
    stats = CleaningStats(
        input_count=100,
        removed_empty_patch=2,
        removed_short_msg=5,
        removed_noise_phrase=3,
        removed_no_alpha=1,
        removed_duplicate=4,
        output_count=85,
    )
    assert stats.input_count == 100
    assert stats.output_count == 85


# ---------------------------------------------------------------------------
# Filter: empty patch
# ---------------------------------------------------------------------------


def test_empty_patch_is_removed() -> None:
    examples = [_raw(patch=""), _raw(patch="   "), _raw()]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


def test_nonempty_patch_is_kept() -> None:
    examples = [_raw(patch="@@ -1 +1 @@\n-old\n+new")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


# ---------------------------------------------------------------------------
# Filter: short message (< 20 chars)
# ---------------------------------------------------------------------------


def test_short_msg_is_removed() -> None:
    examples = [_raw(msg="ok"), _raw(msg="LGTM"), _raw(msg="x" * 19)]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 0


def test_exactly_20_char_msg_is_kept() -> None:
    examples = [_raw(msg="x" * 20)]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


def test_long_msg_is_kept() -> None:
    examples = [_raw(msg="This is a proper review comment with reasoning.")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


# ---------------------------------------------------------------------------
# Filter: noise phrases (exact match on stripped lowercase)
# ---------------------------------------------------------------------------


def test_lgtm_exact_match_removed() -> None:
    examples = [_raw(msg="lgtm"), _raw(msg="LGTM"), _raw(msg="LGTM.")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 0


def test_looks_good_removed() -> None:
    examples = [_raw(msg="looks good"), _raw(msg="Looks good!"), _raw(msg="looks good to me")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 0


def test_plus_one_removed() -> None:
    examples = [_raw(msg="+1"), _raw(msg="+1."), _raw(msg="+1 agreed")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 0


def test_done_exact_removed() -> None:
    examples = [_raw(msg="done"), _raw(msg="Done."), _raw(msg="Done!")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 0


def test_substantive_comment_not_removed_by_noise_filter() -> None:
    msg = "looks good but we should add error handling for the null case."
    examples = [_raw(msg=msg)]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


# ---------------------------------------------------------------------------
# Filter: no alphabetic content
# ---------------------------------------------------------------------------


def test_msg_with_no_letters_is_removed() -> None:
    examples = [_raw(msg="12345 + 678"), _raw(msg="???!!!")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 0


def test_msg_with_letters_is_kept() -> None:
    examples = [_raw(msg="Use int not 12345 here.")]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_identical_patch_and_msg_deduped() -> None:
    patch = "@@ -1 +1 @@\n-foo\n+bar"
    msg = "Rename foo to bar everywhere."
    examples = [_raw(patch=patch, msg=msg, id=1), _raw(patch=patch, msg=msg, id=2)]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


def test_same_patch_different_msg_not_deduped() -> None:
    patch = "@@ -1 +1 @@"
    examples = [
        _raw(patch=patch, msg="This is the first review comment.", id=1),
        _raw(patch=patch, msg="This is a different review comment.", id=2),
    ]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 2


def test_same_msg_different_patch_not_deduped() -> None:
    msg = "Consider adding error handling here."
    examples = [
        _raw(patch="@@ -1 +1 @@\n-a\n+b", msg=msg, id=1),
        _raw(patch="@@ -2 +2 @@\n-x\n+y", msg=msg, id=2),
    ]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 2


def test_first_occurrence_kept_on_dedup() -> None:
    patch = "@@ -1 +1 @@"
    msg = "Add a docstring to this function."
    examples = [_raw(patch=patch, msg=msg, id=10), _raw(patch=patch, msg=msg, id=20)]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 1


# ---------------------------------------------------------------------------
# CleaningStats accuracy
# ---------------------------------------------------------------------------


def test_stats_reflect_removed_counts() -> None:
    cleaner = DataCleaner()
    examples = [
        _raw(patch="", msg="fine comment here"),  # removed: empty patch
        _raw(patch="p", msg="ok"),  # removed: short msg
        _raw(patch="p", msg="lgtm"),  # removed: noise phrase
        _raw(patch="p", msg="123"),  # removed: no alpha + short
        _raw(patch="p", msg="proper review comment here."),  # kept
        _raw(patch="p", msg="proper review comment here.", id=2),  # dedup
    ]
    cleaned, stats = cleaner.clean_with_stats(examples)
    assert stats.input_count == 6
    assert stats.output_count == 1
    assert stats.output_count == len(cleaned)


def test_clean_returns_iterable_of_clean_examples() -> None:
    examples = [_raw(), _raw(id=2, msg="Another good review comment.")]
    result = _clean_all(examples)
    assert all(isinstance(e, CleanExample) for e in result)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty() -> None:
    cleaned = _clean_all([])
    assert cleaned == []


def test_all_good_examples_pass_through() -> None:
    examples = [
        _raw(id=i, msg=f"Review comment number {i} with good detail.", patch=f"@@ -{i} +{i} @@")
        for i in range(1, 11)
    ]
    cleaned = _clean_all(examples)
    assert len(cleaned) == 10
