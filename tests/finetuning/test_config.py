"""Tests for finetuning.config.

These tests lock down the QLoRA hyperparameters and quantization settings for
OpenRabbit-Reviewer-v1. They run without a GPU or any ML library installed,
because TrainingConfig is a pure Pydantic model.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from finetuning.config import TrainingConfig

# The seven linear layers Qwen2.5-Coder exposes. LoRA targets all of them per
# the QLoRA paper's "target all linear layers for max performance" guidance.
_EXPECTED_TARGET_MODULES = {
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
}


# ---------------------------------------------------------------------------
# Defaults: base model + LoRA
# ---------------------------------------------------------------------------


def test_default_base_model_is_qwen_coder_7b() -> None:
    config = TrainingConfig()
    assert config.model_name == "Qwen/Qwen2.5-Coder-7B-Instruct"


def test_default_lora_targets_all_seven_linear_layers() -> None:
    config = TrainingConfig()
    assert set(config.lora_target_modules) == _EXPECTED_TARGET_MODULES


def test_default_lora_rank() -> None:
    config = TrainingConfig()
    assert config.lora_r == 16


def test_default_lora_alpha_is_double_rank() -> None:
    config = TrainingConfig()
    # 2:1 alpha-to-rank ratio is the modern recommended scaling.
    assert config.lora_alpha == 32


def test_default_lora_dropout() -> None:
    config = TrainingConfig()
    assert config.lora_dropout == pytest.approx(0.05)


def test_default_lora_bias_is_none() -> None:
    config = TrainingConfig()
    assert config.lora_bias == "none"


# ---------------------------------------------------------------------------
# Defaults: 4-bit quantization
# ---------------------------------------------------------------------------


def test_default_load_in_4bit_is_true() -> None:
    config = TrainingConfig()
    assert config.load_in_4bit is True


def test_default_quant_type_is_nf4() -> None:
    config = TrainingConfig()
    assert config.bnb_4bit_quant_type == "nf4"


def test_default_compute_dtype_is_bfloat16() -> None:
    config = TrainingConfig()
    assert config.bnb_4bit_compute_dtype == "bfloat16"


def test_default_double_quant_is_enabled() -> None:
    config = TrainingConfig()
    assert config.bnb_4bit_use_double_quant is True


# ---------------------------------------------------------------------------
# Defaults: training hyperparameters
# ---------------------------------------------------------------------------


def test_default_epochs() -> None:
    assert TrainingConfig().num_train_epochs == 2


def test_default_learning_rate() -> None:
    assert TrainingConfig().learning_rate == pytest.approx(2e-4)


def test_default_effective_batch_size_is_sixteen() -> None:
    config = TrainingConfig()
    # batch 2 * grad accum 8 = effective 16
    assert config.effective_batch_size == 16


def test_default_scheduler_is_cosine() -> None:
    assert TrainingConfig().lr_scheduler_type == "cosine"


def test_default_optimizer_is_paged_adamw_8bit() -> None:
    assert TrainingConfig().optim == "paged_adamw_8bit"


def test_default_max_seq_length() -> None:
    # Patch p99 is ~1254 chars; 2048 tokens comfortably fits diff + prompt.
    assert TrainingConfig().max_seq_length == 2048


def test_default_uses_bf16_not_fp16() -> None:
    config = TrainingConfig()
    assert config.bf16 is True
    assert config.fp16 is False


def test_default_gradient_checkpointing_enabled() -> None:
    assert TrainingConfig().gradient_checkpointing is True


# ---------------------------------------------------------------------------
# effective_batch_size derives from batch * grad_accum
# ---------------------------------------------------------------------------


def test_effective_batch_size_recomputes_on_override() -> None:
    config = TrainingConfig(per_device_train_batch_size=4, gradient_accumulation_steps=4)
    assert config.effective_batch_size == 16


def test_effective_batch_size_with_single_batch() -> None:
    config = TrainingConfig(per_device_train_batch_size=1, gradient_accumulation_steps=32)
    assert config.effective_batch_size == 32


# ---------------------------------------------------------------------------
# Validation: LoRA
# ---------------------------------------------------------------------------


def test_lora_rank_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(lora_r=0)


def test_lora_alpha_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(lora_alpha=0)


def test_lora_dropout_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(lora_dropout=-0.1)


def test_lora_dropout_rejects_above_one() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(lora_dropout=1.5)


def test_lora_dropout_accepts_zero() -> None:
    config = TrainingConfig(lora_dropout=0.0)
    assert config.lora_dropout == 0.0


def test_empty_target_modules_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(lora_target_modules=[])


# ---------------------------------------------------------------------------
# Validation: quantization
# ---------------------------------------------------------------------------


def test_quant_type_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(bnb_4bit_quant_type="int8")


def test_quant_type_accepts_fp4() -> None:
    config = TrainingConfig(bnb_4bit_quant_type="fp4")
    assert config.bnb_4bit_quant_type == "fp4"


def test_compute_dtype_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(bnb_4bit_compute_dtype="int8")


def test_compute_dtype_accepts_float16() -> None:
    config = TrainingConfig(bnb_4bit_compute_dtype="float16")
    assert config.bnb_4bit_compute_dtype == "float16"


# ---------------------------------------------------------------------------
# Validation: training
# ---------------------------------------------------------------------------


def test_learning_rate_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(learning_rate=0.0)


def test_epochs_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(num_train_epochs=0)


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(per_device_train_batch_size=0)


def test_grad_accum_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(gradient_accumulation_steps=0)


def test_max_seq_length_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(max_seq_length=0)


def test_scheduler_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        TrainingConfig(lr_scheduler_type="exponential")


def test_scheduler_accepts_linear() -> None:
    config = TrainingConfig(lr_scheduler_type="linear")
    assert config.lr_scheduler_type == "linear"


def test_bf16_and_fp16_both_true_rejected() -> None:
    # Mixed precision is one-or-the-other, never both.
    with pytest.raises(ValidationError):
        TrainingConfig(bf16=True, fp16=True)


def test_fp16_only_is_allowed() -> None:
    config = TrainingConfig(bf16=False, fp16=True)
    assert config.fp16 is True
    assert config.bf16 is False


# ---------------------------------------------------------------------------
# Overrides and serialization (used by the YAML config in OP-28)
# ---------------------------------------------------------------------------


def test_overrides_apply() -> None:
    config = TrainingConfig(lora_r=64, lora_alpha=16, num_train_epochs=3)
    assert config.lora_r == 64
    assert config.lora_alpha == 16
    assert config.num_train_epochs == 3


def test_round_trips_through_dict() -> None:
    config = TrainingConfig(lora_r=32)
    restored = TrainingConfig(**config.model_dump())
    assert restored.lora_r == 32
    assert restored == config


def test_output_dir_has_sensible_default() -> None:
    config = TrainingConfig()
    assert "openrabbit-reviewer" in config.output_dir
