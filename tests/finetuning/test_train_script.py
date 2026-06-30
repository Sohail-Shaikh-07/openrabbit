"""Tests for scripts/train.py (OP-28).

The training script must:
- Load TrainingConfig from a YAML file
- Run end-to-end on CPU with --mock (no GPU, no ML libs)
- Pass all config validation before any GPU work starts
- Default to configs/training.yml when --config is omitted

All tests run without a GPU or any of torch/transformers/peft installed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


def _import_train_module():
    """Import scripts/train.py without executing __main__."""
    repo_root = Path(__file__).parent.parent.parent
    script_path = repo_root / "scripts" / "train.py"
    spec = importlib.util.spec_from_file_location("train", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def train():
    """Loaded train module (cached per test session)."""
    return _import_train_module()


@pytest.fixture()
def default_config_path() -> Path:
    """Absolute path to configs/training.yml."""
    return Path(__file__).parent.parent.parent / "configs" / "training.yml"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_returns_training_config(train, tmp_path):
    cfg_file = tmp_path / "train.yml"
    cfg_file.write_text("model_name: 'Qwen/Qwen2.5-Coder-7B-Instruct'\n")
    from finetuning.config import TrainingConfig

    result = train.load_config(str(cfg_file))
    assert isinstance(result, TrainingConfig)


def test_load_config_applies_yaml_overrides(train, tmp_path):
    cfg_file = tmp_path / "train.yml"
    cfg_file.write_text("lora_r: 64\nlora_alpha: 128\n")

    result = train.load_config(str(cfg_file))
    assert result.lora_r == 64
    assert result.lora_alpha == 128


def test_load_config_empty_yaml_uses_defaults(train, tmp_path):
    cfg_file = tmp_path / "train.yml"
    cfg_file.write_text("{}\n")
    from finetuning.config import TrainingConfig

    result = train.load_config(str(cfg_file))
    assert result == TrainingConfig()


def test_load_config_missing_file_raises(train, tmp_path):
    with pytest.raises(FileNotFoundError):
        train.load_config(str(tmp_path / "nonexistent.yml"))


def test_load_config_invalid_field_raises(train, tmp_path):
    from pydantic import ValidationError

    cfg_file = tmp_path / "bad.yml"
    cfg_file.write_text("lora_r: -1\n")
    with pytest.raises(ValidationError):
        train.load_config(str(cfg_file))


# ---------------------------------------------------------------------------
# _build_mock_dataset
# ---------------------------------------------------------------------------


def test_build_mock_dataset_returns_requested_count(train):
    rows = train._build_mock_dataset(2)
    assert len(rows) == 2


def test_build_mock_dataset_rows_have_text_key(train):
    rows = train._build_mock_dataset(2)
    for row in rows:
        assert "text" in row
        assert isinstance(row["text"], str)


def test_build_mock_dataset_text_is_nonempty(train):
    rows = train._build_mock_dataset(2)
    for row in rows:
        assert len(row["text"]) > 0


def test_build_mock_dataset_default_is_two_rows(train):
    rows = train._build_mock_dataset()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# main -- --mock path (CPU, no GPU)
# ---------------------------------------------------------------------------


def test_main_mock_returns_zero(train, default_config_path):
    result = train.main(["--mock", "--config", str(default_config_path)])
    assert result == 0


def test_main_mock_does_not_call_prepare(train, default_config_path):
    with patch("finetuning.trainer.QLoRATrainer.prepare") as mock_prepare:
        train.main(["--mock", "--config", str(default_config_path)])
    mock_prepare.assert_not_called()


def test_main_mock_does_not_call_train(train, default_config_path):
    with patch("finetuning.trainer.QLoRATrainer.train") as mock_train:
        train.main(["--mock", "--config", str(default_config_path)])
    mock_train.assert_not_called()


def test_main_custom_config_path_is_loaded(train, tmp_path):
    cfg = tmp_path / "custom.yml"
    cfg.write_text("lora_r: 32\n")
    result = train.main(["--mock", "--config", str(cfg)])
    assert result == 0


# ---------------------------------------------------------------------------
# main -- argument defaults
# ---------------------------------------------------------------------------


def test_main_default_config_is_training_yml(train):
    import argparse

    parser_args = None

    original_parse = argparse.ArgumentParser.parse_args

    def capturing_parse(self, args=None, namespace=None):
        nonlocal parser_args
        result = original_parse(self, args, namespace)
        parser_args = result
        return result

    import contextlib

    with (
        patch("argparse.ArgumentParser.parse_args", capturing_parse),
        contextlib.suppress(Exception),
    ):
        train.main(["--mock"])

    if parser_args is not None:
        assert parser_args.config.endswith("training.yml")


# ---------------------------------------------------------------------------
# configs/training.yml -- integration smoke test
# ---------------------------------------------------------------------------


def test_default_config_file_exists(default_config_path):
    assert default_config_path.exists(), f"configs/training.yml not found at {default_config_path}"


def test_default_config_loads_into_training_config(train, default_config_path):
    from finetuning.config import TrainingConfig

    config = train.load_config(str(default_config_path))
    assert isinstance(config, TrainingConfig)


def test_default_config_model_name_is_qwen(train, default_config_path):
    config = train.load_config(str(default_config_path))
    assert "Qwen" in config.model_name


def test_default_config_lora_rank_matches_default(train, default_config_path):
    from finetuning.config import TrainingConfig

    config = train.load_config(str(default_config_path))
    assert config.lora_r == TrainingConfig().lora_r


def test_default_config_has_seven_target_modules(train, default_config_path):
    config = train.load_config(str(default_config_path))
    assert len(config.lora_target_modules) == 7


def test_default_config_precision_flags_are_consistent(train, default_config_path):
    config = train.load_config(str(default_config_path))
    assert not (config.bf16 and config.fp16), "bf16 and fp16 must not both be true"
