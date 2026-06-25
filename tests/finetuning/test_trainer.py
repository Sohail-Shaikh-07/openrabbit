"""Tests for finetuning.trainer.

QLoRATrainer must construct and expose its config-derived plumbing without a
GPU or any of torch/transformers/peft/bitsandbytes installed. The heavy model
load only happens inside prepare(), which is mocked here.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from finetuning.config import TrainingConfig
from finetuning.trainer import QLoRATrainer

# ---------------------------------------------------------------------------
# Construction (no GPU, no ML libs)
# ---------------------------------------------------------------------------


def test_trainer_constructs_from_config() -> None:
    trainer = QLoRATrainer(TrainingConfig())
    assert trainer.config.model_name == "Qwen/Qwen2.5-Coder-7B-Instruct"


def test_trainer_uses_default_config_when_none_given() -> None:
    trainer = QLoRATrainer()
    assert isinstance(trainer.config, TrainingConfig)


def test_model_is_none_before_prepare() -> None:
    trainer = QLoRATrainer()
    assert trainer.model is None
    assert trainer.tokenizer is None


# ---------------------------------------------------------------------------
# bitsandbytes config dict (pure, no GPU)
# ---------------------------------------------------------------------------


def test_quantization_kwargs_reflect_config() -> None:
    config = TrainingConfig(bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype="bfloat16")
    trainer = QLoRATrainer(config)
    kwargs = trainer.quantization_kwargs()
    assert kwargs["load_in_4bit"] is True
    assert kwargs["bnb_4bit_quant_type"] == "nf4"
    assert kwargs["bnb_4bit_use_double_quant"] is True
    assert kwargs["bnb_4bit_compute_dtype"] == "bfloat16"


def test_quantization_kwargs_omitted_when_not_4bit() -> None:
    trainer = QLoRATrainer(TrainingConfig(load_in_4bit=False))
    assert trainer.quantization_kwargs() == {}


# ---------------------------------------------------------------------------
# LoRA config dict (pure, no GPU)
# ---------------------------------------------------------------------------


def test_lora_kwargs_reflect_config() -> None:
    config = TrainingConfig(lora_r=16, lora_alpha=32, lora_dropout=0.05)
    trainer = QLoRATrainer(config)
    kwargs = trainer.lora_kwargs()
    assert kwargs["r"] == 16
    assert kwargs["lora_alpha"] == 32
    assert kwargs["lora_dropout"] == pytest.approx(0.05)
    assert kwargs["bias"] == "none"
    assert kwargs["task_type"] == "CAUSAL_LM"


def test_lora_kwargs_target_modules_match_config() -> None:
    trainer = QLoRATrainer(TrainingConfig())
    kwargs = trainer.lora_kwargs()
    assert set(kwargs["target_modules"]) == {
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    }


# ---------------------------------------------------------------------------
# training_arguments dict (pure, no GPU)
# ---------------------------------------------------------------------------


def test_training_arguments_reflect_config() -> None:
    config = TrainingConfig(
        num_train_epochs=2,
        learning_rate=2e-4,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=8,
    )
    trainer = QLoRATrainer(config)
    args = trainer.training_arguments_kwargs()
    assert args["num_train_epochs"] == 2
    assert args["learning_rate"] == pytest.approx(2e-4)
    assert args["per_device_train_batch_size"] == 2
    assert args["gradient_accumulation_steps"] == 8
    assert args["lr_scheduler_type"] == "cosine"
    assert args["optim"] == "paged_adamw_8bit"
    assert args["output_dir"] == config.output_dir


def test_training_arguments_precision_flags() -> None:
    trainer = QLoRATrainer(TrainingConfig(bf16=True, fp16=False))
    args = trainer.training_arguments_kwargs()
    assert args["bf16"] is True
    assert args["fp16"] is False
    assert args["gradient_checkpointing"] is True


# ---------------------------------------------------------------------------
# prepare() lazily imports ML libs (mocked)
# ---------------------------------------------------------------------------


def test_prepare_loads_model_and_tokenizer() -> None:
    trainer = QLoRATrainer(TrainingConfig())

    fake_model = MagicMock(name="model")
    fake_tokenizer = MagicMock(name="tokenizer")

    with (
        patch.object(trainer, "_load_base_model", return_value=fake_model) as load_model,
        patch.object(trainer, "_load_tokenizer", return_value=fake_tokenizer) as load_tok,
        patch.object(trainer, "_apply_lora", side_effect=lambda m: m) as apply_lora,
    ):
        trainer.prepare()

    load_model.assert_called_once()
    load_tok.assert_called_once()
    apply_lora.assert_called_once_with(fake_model)
    assert trainer.model is fake_model
    assert trainer.tokenizer is fake_tokenizer


def test_prepare_is_idempotent() -> None:
    trainer = QLoRATrainer(TrainingConfig())
    fake_model = MagicMock()
    fake_tokenizer = MagicMock()

    with (
        patch.object(trainer, "_load_base_model", return_value=fake_model) as load_model,
        patch.object(trainer, "_load_tokenizer", return_value=fake_tokenizer),
        patch.object(trainer, "_apply_lora", side_effect=lambda m: m),
    ):
        trainer.prepare()
        trainer.prepare()

    # Second prepare() should not reload the base model.
    load_model.assert_called_once()


def test_train_raises_if_not_prepared() -> None:
    trainer = QLoRATrainer(TrainingConfig())
    with pytest.raises(RuntimeError, match="prepare"):
        trainer.train(train_dataset=MagicMock(), eval_dataset=MagicMock())
