"""Tests for finetuning.formatter."""

from __future__ import annotations

import pytest

from finetuning.cleaner import CleanExample
from finetuning.formatter import InstructionFormatter, Split, TrainingExample

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(
    patch: str = "@@ -1 +1 @@\n-foo = 1\n+foo = None",
    msg: str = "Assigning None directly may cause a NullPointerException downstream.",
) -> CleanExample:
    return CleanExample(patch=patch, msg=msg)


# ---------------------------------------------------------------------------
# TrainingExample
# ---------------------------------------------------------------------------


def test_training_example_has_required_fields() -> None:
    ex = TrainingExample(
        text="<|im_start|>...<|im_end|>",
        messages=[{"role": "user", "content": "hi"}],
        split=Split.train,
    )
    assert ex.text
    assert ex.messages
    assert ex.split is Split.train


def test_training_example_is_frozen() -> None:
    ex = TrainingExample(text="t", messages=[], split=Split.train)
    with pytest.raises((AttributeError, TypeError)):
        ex.text = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Split enum
# ---------------------------------------------------------------------------


def test_split_values_exist() -> None:
    assert Split.train
    assert Split.val
    assert Split.test


# ---------------------------------------------------------------------------
# InstructionFormatter.format_single
# ---------------------------------------------------------------------------


def test_format_single_returns_training_example() -> None:
    ex = _clean()
    result = InstructionFormatter().format_single(ex, split=Split.train)
    assert isinstance(result, TrainingExample)


def test_format_single_text_contains_patch() -> None:
    patch = "@@ -3 +3 @@\n-x = 1\n+x = None"
    ex = _clean(patch=patch)
    result = InstructionFormatter().format_single(ex, split=Split.train)
    assert patch in result.text


def test_format_single_text_contains_msg() -> None:
    msg = "This null assignment will break the downstream caller."
    ex = _clean(msg=msg)
    result = InstructionFormatter().format_single(ex, split=Split.train)
    assert msg in result.text


def test_format_single_messages_has_system_user_assistant() -> None:
    ex = _clean()
    result = InstructionFormatter().format_single(ex, split=Split.train)
    roles = [m["role"] for m in result.messages]
    assert "system" in roles
    assert "user" in roles
    assert "assistant" in roles


def test_format_single_assistant_content_is_msg() -> None:
    msg = "Use a default value instead of None to prevent null reference errors."
    ex = _clean(msg=msg)
    result = InstructionFormatter().format_single(ex, split=Split.train)
    assistant = next(m for m in result.messages if m["role"] == "assistant")
    assert msg in assistant["content"]


def test_format_single_user_content_contains_patch() -> None:
    patch = "@@ -10 +10 @@\n-return x\n+return None"
    ex = _clean(patch=patch)
    result = InstructionFormatter().format_single(ex, split=Split.train)
    user = next(m for m in result.messages if m["role"] == "user")
    assert patch in user["content"]


def test_format_single_text_uses_qwen_im_tokens() -> None:
    ex = _clean()
    result = InstructionFormatter().format_single(ex, split=Split.train)
    assert "<|im_start|>" in result.text
    assert "<|im_end|>" in result.text


def test_format_single_split_is_set_correctly() -> None:
    ex = _clean()
    assert InstructionFormatter().format_single(ex, split=Split.val).split is Split.val
    assert InstructionFormatter().format_single(ex, split=Split.test).split is Split.test


# ---------------------------------------------------------------------------
# InstructionFormatter.format_dataset
# ---------------------------------------------------------------------------


def test_format_dataset_returns_split_dict() -> None:
    examples = [_clean() for _ in range(20)]
    result = InstructionFormatter().format_dataset(examples)
    assert Split.train in result
    assert Split.val in result
    assert Split.test in result


def test_format_dataset_total_equals_input_count() -> None:
    examples = [_clean() for _ in range(100)]
    result = InstructionFormatter().format_dataset(examples)
    total = sum(len(v) for v in result.values())
    assert total == 100


def test_format_dataset_split_proportions(tmp_path: object) -> None:
    examples = [_clean() for _ in range(200)]
    result = InstructionFormatter(train_ratio=0.9, val_ratio=0.05).format_dataset(examples)
    train_pct = len(result[Split.train]) / 200
    assert 0.85 <= train_pct <= 0.95


def test_format_dataset_split_is_deterministic() -> None:
    examples = [
        CleanExample(patch=f"patch {i}", msg=f"review comment number {i} is here.")
        for i in range(50)
    ]
    r1 = InstructionFormatter(seed=42).format_dataset(examples)
    r2 = InstructionFormatter(seed=42).format_dataset(examples)
    assert len(r1[Split.train]) == len(r2[Split.train])


def test_format_dataset_each_example_has_correct_split_field() -> None:
    examples = [_clean() for _ in range(30)]
    result = InstructionFormatter().format_dataset(examples)
    for split, items in result.items():
        assert all(item.split is split for item in items)


def test_format_dataset_handles_empty_input() -> None:
    result = InstructionFormatter().format_dataset([])
    assert all(len(v) == 0 for v in result.values())
