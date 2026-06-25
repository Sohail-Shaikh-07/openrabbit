"""Training configuration for OpenRabbit-Reviewer-v1 QLoRA fine-tuning.

:class:`TrainingConfig` is a pure Pydantic model holding every hyperparameter
for the QLoRA run. It carries no dependency on torch, transformers, peft, or
bitsandbytes, so it imports and validates anywhere, including CI without a GPU.

The defaults encode the locked decisions for OpenRabbit-Reviewer-v1:

LoRA
    Target all seven Qwen2.5-Coder linear projections
    (``q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj``).
    The QLoRA paper shows that adapting every linear layer, rather than just
    the attention query/value projections, recovers most of the quality gap to
    full fine-tuning.

    Rank 16 with alpha 32 gives a 2:1 alpha-to-rank scaling, the modern
    recommended ratio. This produces a small adapter (~80-150 MB) that trains
    quickly and resists overfitting on the ~100K-example dataset. The original
    plan listed rank 64 / alpha 16; that 0.25 scaling is unusually weak, so the
    defaults here follow current best practice. Both values remain overridable.

Quantization
    NF4 (NormalFloat4) is the QLoRA paper's quantization type for
    normally-distributed weights. Double quantization saves a further ~0.4
    bits per parameter. Compute runs in bfloat16, which has a wider dynamic
    range than float16 and is supported by A100 and RTX 4090 GPUs.

Training
    Two epochs at 2e-4 with a cosine schedule and 3% warmup. Effective batch
    size 16 (per-device 2 times gradient accumulation 8). The paged 8-bit
    AdamW optimizer keeps optimizer state off the GPU when memory is tight.
    Sequence length 2048 comfortably fits the diff plus prompt: the dataset's
    99th-percentile patch is ~1254 characters.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

QuantType = Literal["nf4", "fp4"]
ComputeDtype = Literal["bfloat16", "float16"]
SchedulerType = Literal["linear", "cosine", "constant", "constant_with_warmup"]
LoraBias = Literal["none", "all", "lora_only"]

_DEFAULT_TARGET_MODULES: tuple[str, ...] = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)


class TrainingConfig(BaseModel):
    """All hyperparameters for one QLoRA fine-tuning run.

    Every field has a production-ready default. Override any field at
    construction time or load the whole object from a YAML mapping in the
    standalone training script (OP-28).
    """

    # -- Base model --------------------------------------------------------
    model_name: str = "Qwen/Qwen2.5-Coder-7B-Instruct"

    # -- LoRA --------------------------------------------------------------
    lora_r: int = Field(default=16, ge=1)
    lora_alpha: int = Field(default=32, ge=1)
    lora_dropout: float = Field(default=0.05, ge=0.0, le=1.0)
    lora_bias: LoraBias = "none"
    lora_target_modules: list[str] = Field(default_factory=lambda: list(_DEFAULT_TARGET_MODULES))

    # -- 4-bit quantization ------------------------------------------------
    load_in_4bit: bool = True
    bnb_4bit_quant_type: QuantType = "nf4"
    bnb_4bit_compute_dtype: ComputeDtype = "bfloat16"
    bnb_4bit_use_double_quant: bool = True

    # -- Training schedule -------------------------------------------------
    num_train_epochs: int = Field(default=2, ge=1)
    per_device_train_batch_size: int = Field(default=2, ge=1)
    gradient_accumulation_steps: int = Field(default=8, ge=1)
    learning_rate: float = Field(default=2e-4, gt=0.0)
    warmup_ratio: float = Field(default=0.03, ge=0.0, le=1.0)
    lr_scheduler_type: SchedulerType = "cosine"
    weight_decay: float = Field(default=0.01, ge=0.0)
    optim: str = "paged_adamw_8bit"
    max_seq_length: int = Field(default=2048, ge=1)

    # -- Precision ---------------------------------------------------------
    bf16: bool = True
    fp16: bool = False
    gradient_checkpointing: bool = True

    # -- IO and reproducibility -------------------------------------------
    output_dir: str = "outputs/openrabbit-reviewer-v1"
    logging_steps: int = Field(default=10, ge=1)
    save_steps: int = Field(default=200, ge=1)
    save_total_limit: int = Field(default=3, ge=1)
    seed: int = 42

    @property
    def effective_batch_size(self) -> int:
        """Per-device batch size times gradient accumulation steps."""
        return self.per_device_train_batch_size * self.gradient_accumulation_steps

    @model_validator(mode="after")
    def _validate_precision(self) -> TrainingConfig:
        if self.bf16 and self.fp16:
            raise ValueError("bf16 and fp16 are mutually exclusive; enable only one")
        if not self.lora_target_modules:
            raise ValueError("lora_target_modules must list at least one module")
        return self
