#!/usr/bin/env python3
"""Standalone QLoRA training entry point for OpenRabbit-Reviewer-v1.

GPU run (RunPod RTX 4090 or Colab T4):
    python scripts/train.py --config configs/training.yml \\
        --data dataset/Comment_Generation/msg-train.jsonl

Local validation on CPU (no dataset, no GPU):
    python scripts/train.py --config configs/training.yml --mock

See scripts/README.md for full platform-specific setup instructions.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

# Allow `python scripts/train.py` to work from the repo root without
# `pip install -e .` by putting src/ on the path automatically.
_src = Path(__file__).resolve().parent.parent / "src"
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import yaml  # noqa: E402

from finetuning.config import TrainingConfig  # noqa: E402
from finetuning.trainer import QLoRATrainer  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = "configs/training.yml"
_DEFAULT_DATA = "dataset/Comment_Generation/msg-train.jsonl"


def load_config(config_path: str) -> TrainingConfig:
    """Load and validate :class:`~finetuning.config.TrainingConfig` from *config_path*.

    Parameters
    ----------
    config_path:
        Path to a YAML file whose keys match :class:`TrainingConfig` field names.
        An empty or missing mapping returns a config with production defaults.

    Raises
    ------
    FileNotFoundError
        If *config_path* does not exist on disk.
    pydantic.ValidationError
        If any field value is invalid (e.g. negative lora_r).
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return TrainingConfig(**data)


def _build_mock_dataset(n: int = 2) -> list[dict[str, Any]]:
    """Return *n* synthetic training rows for local validation.

    Each row has a ``text`` key containing a minimal Qwen chat-format string.
    These rows are used by ``--mock`` to prove the script logic runs end-to-end
    without loading the real dataset or touching any ML library.
    """
    return [
        {
            "text": (
                "<|im_start|>system\n"
                "You are a senior code reviewer.<|im_end|>\n"
                "<|im_start|>user\n"
                f"Review diff:\n```diff\n-x = None\n+x = 1  # mock row {i}\n```"
                "<|im_end|>\n"
                "<|im_start|>assistant\n"
                "No issues found.<|im_end|>\n"
            )
        }
        for i in range(n)
    ]


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, load config, and run training (or mock dry-run).

    Parameters
    ----------
    argv:
        Argument list. Defaults to ``sys.argv[1:]`` when ``None``.

    Returns
    -------
    int
        Exit code: 0 on success, non-zero on error.
    """
    parser = argparse.ArgumentParser(
        description="Train OpenRabbit-Reviewer-v1 via QLoRA.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Full GPU run\n"
            "  python scripts/train.py --config configs/training.yml "
            "--data dataset/Comment_Generation/msg-train.jsonl\n\n"
            "  # CPU dry-run (no GPU, no dataset)\n"
            "  python scripts/train.py --config configs/training.yml --mock\n"
        ),
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG,
        metavar="PATH",
        help=f"Path to YAML training config (default: {_DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--data",
        default=_DEFAULT_DATA,
        metavar="PATH",
        help=f"Path to training JSONL file (default: {_DEFAULT_DATA})",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help=(
            "Dry-run with a 2-row synthetic dataset. "
            "Skips GPU model loading and actual training. "
            "Use this to validate config and script logic on CPU."
        ),
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    trainer = QLoRATrainer(config)

    if args.mock:
        dataset = _build_mock_dataset(2)
        logger.info("[mock] Config loaded: model=%s", config.model_name)
        logger.info(
            "[mock] LoRA: rank=%d  alpha=%d  dropout=%.2f",
            config.lora_r,
            config.lora_alpha,
            config.lora_dropout,
        )
        logger.info(
            "[mock] Training: epochs=%d  lr=%s  batch=%d",
            config.num_train_epochs,
            config.learning_rate,
            config.effective_batch_size,
        )
        logger.info("[mock] Output dir: %s", config.output_dir)
        logger.info("[mock] Dataset: %d synthetic rows", len(dataset))
        logger.info("[mock] Skipping prepare() and train() -- no GPU required")
        return 0

    # Full training path: requires GPU + ML stack (torch, transformers, peft, trl).
    from finetuning.cleaner import DataCleaner
    from finetuning.dataset import DatasetLoader
    from finetuning.formatter import InstructionFormatter, Split

    loader = DatasetLoader()
    cleaner = DataCleaner()
    formatter = InstructionFormatter()

    data_path = Path(args.data)
    logger.info("Loading dataset from %s", data_path)
    raw = loader.load(data_path)

    logger.info("Cleaning examples...")
    cleaned, stats = cleaner.clean_with_stats(raw)
    logger.info(
        "Cleaned: kept=%d  dropped=%d  (input=%d)",
        stats.output_count,
        stats.input_count - stats.output_count,
        stats.input_count,
    )

    splits = formatter.format_dataset(cleaned)
    train_data = splits[Split.train]
    val_data = splits[Split.val]
    logger.info(
        "Splits: train=%d  val=%d  test=%d",
        len(train_data),
        len(val_data),
        len(splits[Split.test]),
    )

    logger.info("Preparing model (loading %s in 4-bit)...", config.model_name)
    trainer.prepare()

    logger.info("Starting training: %d epoch(s)...", config.num_train_epochs)
    trainer.train(train_data, val_data)

    adapter_dir = trainer.save()
    logger.info("Adapter saved to %s", adapter_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
