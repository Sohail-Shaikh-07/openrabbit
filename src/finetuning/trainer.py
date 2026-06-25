"""QLoRA trainer for OpenRabbit-Reviewer-v1.

:class:`QLoRATrainer` wires together the four config-derived pieces of a QLoRA
run -- 4-bit quantization, the LoRA adapter, the training arguments, and the
supervised fine-tuning loop -- around the locked :class:`~finetuning.config.TrainingConfig`.

Heavy dependencies (torch, transformers, peft, trl, bitsandbytes) are imported
*lazily* inside the methods that need them. The class therefore constructs and
exposes its config-derived plumbing on any machine, including CI without a GPU.
Only :meth:`prepare` and :meth:`train` require the ML stack and a CUDA device.

The dict-builder methods (:meth:`quantization_kwargs`, :meth:`lora_kwargs`,
:meth:`training_arguments_kwargs`) are pure functions of the config. They are
what the standalone training script (OP-28) feeds into ``BitsAndBytesConfig``,
``LoraConfig``, and ``SFTConfig`` respectively, and they are fully unit-tested.

Usage on a GPU machine::

    from finetuning.config import TrainingConfig
    from finetuning.trainer import QLoRATrainer

    trainer = QLoRATrainer(TrainingConfig())
    trainer.prepare()                       # loads Qwen in 4-bit, attaches LoRA
    trainer.train(train_dataset, eval_dataset)
    trainer.save("outputs/adapter")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from finetuning.config import TrainingConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

_TASK_TYPE = "CAUSAL_LM"


class QLoRATrainer:
    """Drives a QLoRA fine-tuning run from a :class:`TrainingConfig`.

    Parameters
    ----------
    config:
        The locked training configuration. Defaults to a fresh
        :class:`TrainingConfig` with production defaults.
    """

    def __init__(self, config: TrainingConfig | None = None) -> None:
        self.config = config or TrainingConfig()
        self.model: Any = None
        self.tokenizer: Any = None
        self._trainer: Any = None

    # ------------------------------------------------------------------
    # Config-derived kwargs (pure, no GPU, no ML libs)
    # ------------------------------------------------------------------

    def quantization_kwargs(self) -> dict[str, Any]:
        """Return kwargs for ``transformers.BitsAndBytesConfig``.

        Returns an empty dict when 4-bit loading is disabled, signalling the
        caller to load the model in full precision.
        """
        if not self.config.load_in_4bit:
            return {}
        return {
            "load_in_4bit": True,
            "bnb_4bit_quant_type": self.config.bnb_4bit_quant_type,
            "bnb_4bit_use_double_quant": self.config.bnb_4bit_use_double_quant,
            "bnb_4bit_compute_dtype": self.config.bnb_4bit_compute_dtype,
        }

    def lora_kwargs(self) -> dict[str, Any]:
        """Return kwargs for ``peft.LoraConfig``."""
        return {
            "r": self.config.lora_r,
            "lora_alpha": self.config.lora_alpha,
            "lora_dropout": self.config.lora_dropout,
            "bias": self.config.lora_bias,
            "target_modules": list(self.config.lora_target_modules),
            "task_type": _TASK_TYPE,
        }

    def training_arguments_kwargs(self) -> dict[str, Any]:
        """Return kwargs for the TRL ``SFTConfig`` / ``TrainingArguments``."""
        return {
            "output_dir": self.config.output_dir,
            "num_train_epochs": self.config.num_train_epochs,
            "per_device_train_batch_size": self.config.per_device_train_batch_size,
            "gradient_accumulation_steps": self.config.gradient_accumulation_steps,
            "learning_rate": self.config.learning_rate,
            "warmup_ratio": self.config.warmup_ratio,
            "lr_scheduler_type": self.config.lr_scheduler_type,
            "weight_decay": self.config.weight_decay,
            "optim": self.config.optim,
            "bf16": self.config.bf16,
            "fp16": self.config.fp16,
            "gradient_checkpointing": self.config.gradient_checkpointing,
            "logging_steps": self.config.logging_steps,
            "save_steps": self.config.save_steps,
            "save_total_limit": self.config.save_total_limit,
            "seed": self.config.seed,
        }

    # ------------------------------------------------------------------
    # GPU-bound lifecycle (lazy ML imports)
    # ------------------------------------------------------------------

    def prepare(self) -> None:
        """Load the base model in 4-bit and attach the LoRA adapter.

        Idempotent: calling :meth:`prepare` more than once is a no-op after the
        first successful load.
        """
        if self.model is not None:
            return
        self.tokenizer = self._load_tokenizer()
        base_model = self._load_base_model()
        self.model = self._apply_lora(base_model)

    def train(self, train_dataset: Any, eval_dataset: Any | None = None) -> Any:
        """Run supervised fine-tuning over *train_dataset*.

        Raises
        ------
        RuntimeError
            If :meth:`prepare` has not been called yet.
        """
        if self.model is None:
            raise RuntimeError("Call prepare() before train() to load the model")
        self._trainer = self._build_sft_trainer(train_dataset, eval_dataset)
        return self._trainer.train()

    def save(
        self, output_dir: str | None = None
    ) -> str:  # pragma: no cover - needs a trained model
        """Persist the trained LoRA adapter and tokenizer to *output_dir*.

        Returns the directory the adapter was written to.
        """
        if self.model is None:
            raise RuntimeError("Nothing to save; call prepare() and train() first")
        target = output_dir or self.config.output_dir
        self.model.save_pretrained(target)
        if self.tokenizer is not None:
            self.tokenizer.save_pretrained(target)
        return target

    # ------------------------------------------------------------------
    # Lazy ML-stack helpers (overridden in tests; GPU-only, so not measured)
    # ------------------------------------------------------------------

    def _load_tokenizer(self) -> Any:  # pragma: no cover - requires transformers + GPU
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(self.config.model_name)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        return tokenizer

    def _load_base_model(self) -> Any:  # pragma: no cover - requires transformers + GPU
        import torch
        from transformers import AutoModelForCausalLM, BitsAndBytesConfig

        quant_kwargs = self.quantization_kwargs()
        bnb_config = None
        if quant_kwargs:
            compute_dtype = getattr(torch, self.config.bnb_4bit_compute_dtype)
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=quant_kwargs["load_in_4bit"],
                bnb_4bit_quant_type=quant_kwargs["bnb_4bit_quant_type"],
                bnb_4bit_use_double_quant=quant_kwargs["bnb_4bit_use_double_quant"],
                bnb_4bit_compute_dtype=compute_dtype,
            )

        return AutoModelForCausalLM.from_pretrained(
            self.config.model_name,
            quantization_config=bnb_config,
            device_map="auto",
        )

    def _apply_lora(self, base_model: Any) -> Any:  # pragma: no cover - requires peft + GPU
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

        if self.config.load_in_4bit:
            base_model = prepare_model_for_kbit_training(
                base_model,
                use_gradient_checkpointing=self.config.gradient_checkpointing,
            )
        lora_config = LoraConfig(**self.lora_kwargs())
        return get_peft_model(base_model, lora_config)

    def _build_sft_trainer(  # pragma: no cover - requires trl + GPU
        self, train_dataset: Any, eval_dataset: Any | None
    ) -> Any:
        from trl import SFTConfig, SFTTrainer

        sft_config = SFTConfig(
            max_seq_length=self.config.max_seq_length,
            **self.training_arguments_kwargs(),
        )
        return SFTTrainer(
            model=self.model,
            args=sft_config,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            processing_class=self.tokenizer,
        )
