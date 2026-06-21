"""Instruction formatter for Phase 5 QLoRA fine-tuning.

Transforms :class:`~finetuning.cleaner.CleanExample` objects into
:class:`TrainingExample` objects formatted for Qwen2.5-Coder instruction
tuning. Two representations are produced per example:

* **text** -- the fully rendered string using Qwen's ``<|im_start|>`` /
  ``<|im_end|>`` chat tokens, ready to be tokenized directly.
* **messages** -- a list of ``{"role": ..., "content": ...}`` dicts
  compatible with HuggingFace ``tokenizer.apply_chat_template()``.

The system prompt positions the model as a senior code reviewer so every
fine-tuning example reinforces the review persona.

Usage::

    from finetuning.formatter import InstructionFormatter, Split

    formatter = InstructionFormatter()
    split_data = formatter.format_dataset(clean_examples)
    for example in split_data[Split.train]:
        print(example.text)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

from finetuning.cleaner import CleanExample

_SYSTEM_PROMPT = (
    "You are a senior software engineer performing a pull request code review. "
    "Analyze the diff hunk provided and produce a concise, actionable review comment "
    "that identifies issues, explains why they matter, and suggests a concrete fix. "
    "Focus on correctness, security, performance, and maintainability."
)

_USER_TEMPLATE = "Review the following pull request diff:\n\n```diff\n{patch}\n```"

_IM_START = "<|im_start|>"
_IM_END = "<|im_end|>"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Split(StrEnum):
    """Dataset split label."""

    train = "train"
    val = "val"
    test = "test"


@dataclass(frozen=True)
class TrainingExample:
    """One fine-tuning example in both text and chat-message formats.

    Attributes
    ----------
    text:
        Fully rendered string using Qwen's ``<|im_start|>`` / ``<|im_end|>``
        tokens. Pass directly to the tokenizer.
    messages:
        List of ``{"role": str, "content": str}`` dicts. Pass to
        ``tokenizer.apply_chat_template(messages, tokenize=False)``.
    split:
        Which dataset split this example belongs to.
    """

    text: str
    messages: list[dict[str, str]]
    split: Split


# ---------------------------------------------------------------------------
# InstructionFormatter
# ---------------------------------------------------------------------------


class InstructionFormatter:
    """Converts :class:`~finetuning.cleaner.CleanExample` objects to training format.

    Parameters
    ----------
    train_ratio:
        Fraction of examples assigned to the training split.
    val_ratio:
        Fraction assigned to validation. The remainder goes to test.
    seed:
        Random seed for reproducible shuffling.
    system_prompt:
        Override the default code-review system prompt.
    """

    def __init__(
        self,
        train_ratio: float = 0.90,
        val_ratio: float = 0.05,
        seed: int = 42,
        system_prompt: str = _SYSTEM_PROMPT,
    ) -> None:
        self._train_ratio = train_ratio
        self._val_ratio = val_ratio
        self._seed = seed
        self._system_prompt = system_prompt

    def format_single(self, example: CleanExample, *, split: Split) -> TrainingExample:
        """Return a :class:`TrainingExample` for *example* in *split*."""
        messages = self._build_messages(example)
        text = self._render_text(messages)
        return TrainingExample(text=text, messages=messages, split=split)

    def format_dataset(self, examples: list[CleanExample]) -> dict[Split, list[TrainingExample]]:
        """Shuffle *examples* and split into train/val/test.

        The split is deterministic for a given *seed* so training runs are
        reproducible.

        Parameters
        ----------
        examples:
            Cleaned examples from :class:`~finetuning.cleaner.DataCleaner`.

        Returns
        -------
        dict mapping each :class:`Split` to a list of :class:`TrainingExample`.
        """
        result: dict[Split, list[TrainingExample]] = {
            Split.train: [],
            Split.val: [],
            Split.test: [],
        }
        if not examples:
            return result

        indices = list(range(len(examples)))
        rng = random.Random(self._seed)
        rng.shuffle(indices)

        n = len(indices)
        n_train = int(n * self._train_ratio)
        n_val = int(n * self._val_ratio)

        for pos, idx in enumerate(indices):
            if pos < n_train:
                split = Split.train
            elif pos < n_train + n_val:
                split = Split.val
            else:
                split = Split.test
            result[split].append(self.format_single(examples[idx], split=split))

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_messages(self, example: CleanExample) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": _USER_TEMPLATE.format(patch=example.patch)},
            {"role": "assistant", "content": example.msg},
        ]

    def _render_text(self, messages: list[dict[str, str]]) -> str:
        """Render messages using Qwen's im_start/im_end token format."""
        parts: list[str] = []
        for msg in messages:
            parts.append(f"{_IM_START}{msg['role']}\n{msg['content']}{_IM_END}\n")
        parts.append(f"{_IM_START}assistant\n")
        return "".join(parts)
