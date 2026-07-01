"""LoRA adapter packager for OpenRabbit-Reviewer-v1.

:class:`AdapterPackager` copies a trained adapter from the training output
directory into a clean release directory and writes a HuggingFace-compatible
model card. It can also upload to the Hub via ``huggingface_hub``.

The ``save()`` method has no ML dependencies -- it works on plain file
operations. ``upload()`` requires ``huggingface_hub`` and a valid token.

Usage::

    from finetuning.packager import AdapterPackager

    packager = AdapterPackager()
    info = packager.save(
        source_dir=Path("outputs/openrabbit-reviewer-v1"),
        output_dir=Path("release/openrabbit-reviewer-v1"),
    )
    print(f"Packaged {len(info.files)} files to {info.output_dir}")
    url = packager.upload(info.output_dir, token="hf_...")
    print(f"Uploaded: {url}")
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from huggingface_hub import upload_folder

_DEFAULT_MODEL_ID = "openrabbit/openrabbit-reviewer-v1"
_BASE_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"

_REQUIRED_FILES = ("adapter_model.safetensors", "adapter_config.json")


@dataclass(frozen=True)
class AdapterInfo:
    """Metadata for a packaged adapter.

    Attributes
    ----------
    model_id:
        HuggingFace Hub repo ID (e.g. ``openrabbit/openrabbit-reviewer-v1``).
    output_dir:
        Directory where the packaged files were written.
    files:
        List of all files written to ``output_dir``.
    """

    model_id: str
    output_dir: Path
    files: list[Path] = field(default_factory=list)


class AdapterPackager:
    """Packages a trained LoRA adapter for distribution.

    Parameters
    ----------
    model_id:
        HuggingFace Hub repo ID for the packaged model.
        Defaults to ``openrabbit/openrabbit-reviewer-v1``.
    """

    def __init__(self, model_id: str = _DEFAULT_MODEL_ID) -> None:
        self.model_id = model_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, source_dir: Path, output_dir: Path) -> AdapterInfo:
        """Copy adapter files from ``source_dir`` and write a model card.

        Parameters
        ----------
        source_dir:
            Directory containing the adapter checkpoint produced by
            :class:`~finetuning.trainer.QLoRATrainer`. Must contain
            ``adapter_model.safetensors`` and ``adapter_config.json``.
        output_dir:
            Destination directory for the packaged release files.

        Returns
        -------
        AdapterInfo
            Metadata describing what was written.

        Raises
        ------
        FileNotFoundError
            If ``source_dir`` does not exist or a required file is missing.
        ValueError
            If ``source_dir`` is not a directory.
        """
        if not source_dir.exists():
            raise FileNotFoundError(f"Source directory not found: {source_dir}")
        if not source_dir.is_dir():
            raise ValueError(f"source_dir must be a directory: {source_dir}")

        for name in _REQUIRED_FILES:
            required = source_dir / name
            if not required.exists():
                raise FileNotFoundError(f"Required adapter file missing from {source_dir}: {name}")

        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []

        for name in _REQUIRED_FILES:
            dest = output_dir / name
            shutil.copy2(source_dir / name, dest)
            written.append(dest)

        readme = self._write_model_card(source_dir, output_dir)
        written.append(readme)

        return AdapterInfo(model_id=self.model_id, output_dir=output_dir, files=written)

    def upload(self, output_dir: Path, token: str | None = None) -> str:
        """Upload the packaged adapter to HuggingFace Hub.

        Parameters
        ----------
        output_dir:
            Directory containing the packaged adapter files (from :meth:`save`).
        token:
            HuggingFace Hub write token. Required.

        Returns
        -------
        str
            URL of the uploaded repository.

        Raises
        ------
        ValueError
            If no token is provided.
        """
        if not token:
            raise ValueError(
                "A HuggingFace Hub write token is required for upload. "
                "Pass token='hf_...' or set HF_TOKEN in the environment."
            )

        commit = upload_folder(
            folder_path=output_dir,
            repo_id=self.model_id,
            repo_type="model",
            token=token,
            commit_message="Upload OpenRabbit-Reviewer-v1 LoRA adapter",
        )
        return str(commit.repo_url)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write_model_card(self, source_dir: Path, output_dir: Path) -> Path:
        """Generate a HuggingFace-compatible README.md in ``output_dir``."""
        config_path = source_dir / "adapter_config.json"
        try:
            config = json.loads(config_path.read_text())
        except Exception:
            config = {}

        lora_r = config.get("r", 16)
        lora_alpha = config.get("lora_alpha", 32)
        base_model = config.get("base_model_name_or_path", _BASE_MODEL)

        content = f"""\
---
base_model: {base_model}
library_name: peft
model-index:
  - name: {self.model_id}
    results: []
tags:
  - code-review
  - lora
  - qlora
  - qwen2.5
  - openrabbit
---

# {self.model_id}

A QLoRA fine-tuned adapter for automated pull request code review,
built on top of [{base_model}](https://huggingface.co/{base_model}).

Part of the [OpenRabbit](https://github.com/Sohail-Shaikh-07/openrabbit) project.

## Adapter details

| Parameter | Value |
|-----------|-------|
| Base model | `{base_model}` |
| Method | QLoRA (4-bit NF4 + LoRA) |
| LoRA rank | {lora_r} |
| LoRA alpha | {lora_alpha} |

## Usage

```bash
openrabbit install-model
```

Or manually with PEFT:

```python
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base = AutoModelForCausalLM.from_pretrained("{base_model}", load_in_4bit=True)
model = PeftModel.from_pretrained(base, "{self.model_id}")
tokenizer = AutoTokenizer.from_pretrained("{base_model}")
```

## License

Apache 2.0. See [LICENSE](https://github.com/Sohail-Shaikh-07/openrabbit/blob/main/LICENSE).
"""
        readme = output_dir / "README.md"
        readme.write_text(content, encoding="utf-8")
        return readme
