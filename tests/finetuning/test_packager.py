"""Tests for finetuning.packager (OP-30).

All tests use tmp_path for file I/O. No HuggingFace Hub calls happen --
all network interactions are mocked. The packager is designed so that
save() works without GPU or ML libraries.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from finetuning.packager import AdapterInfo, AdapterPackager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_ID = "openrabbit/openrabbit-reviewer-v1"

_ADAPTER_FILES = {
    "adapter_model.safetensors": b"\x00\x01\x02\x03",  # fake binary
    "adapter_config.json": json.dumps(
        {
            "base_model_name_or_path": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "peft_type": "LORA",
            "r": 16,
            "lora_alpha": 32,
        }
    ).encode(),
}


def _make_source_dir(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    """Create a source adapter directory with the given files."""
    src = tmp_path / "adapter_source"
    src.mkdir()
    for name, content in (files or _ADAPTER_FILES).items():
        (src / name).write_bytes(content)
    return src


# ---------------------------------------------------------------------------
# AdapterInfo dataclass
# ---------------------------------------------------------------------------


def test_adapter_info_fields_accessible(tmp_path: Path) -> None:
    info = AdapterInfo(
        model_id=_MODEL_ID,
        output_dir=tmp_path,
        files=[tmp_path / "adapter_model.safetensors"],
    )
    assert info.model_id == _MODEL_ID
    assert info.output_dir == tmp_path
    assert len(info.files) == 1


def test_adapter_info_is_frozen(tmp_path: Path) -> None:
    info = AdapterInfo(
        model_id=_MODEL_ID,
        output_dir=tmp_path,
        files=[],
    )
    with pytest.raises((AttributeError, TypeError)):
        info.model_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AdapterPackager construction
# ---------------------------------------------------------------------------


def test_packager_default_model_id() -> None:
    p = AdapterPackager()
    assert p.model_id == _MODEL_ID


def test_packager_custom_model_id() -> None:
    p = AdapterPackager(model_id="myorg/my-model")
    assert p.model_id == "myorg/my-model"


# ---------------------------------------------------------------------------
# AdapterPackager.save() -- file operations
# ---------------------------------------------------------------------------


def test_save_creates_output_dir(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    p.save(src, out)

    assert out.is_dir()


def test_save_returns_adapter_info(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    info = p.save(src, out)

    assert isinstance(info, AdapterInfo)


def test_save_adapter_info_output_dir(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    info = p.save(src, out)

    assert info.output_dir == out


def test_save_adapter_info_model_id(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    info = p.save(src, out)

    assert info.model_id == _MODEL_ID


def test_save_copies_safetensors_file(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    p.save(src, out)

    assert (out / "adapter_model.safetensors").exists()


def test_save_copies_adapter_config(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    p.save(src, out)

    assert (out / "adapter_config.json").exists()


def test_save_preserves_file_content(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    p.save(src, out)

    original = (src / "adapter_model.safetensors").read_bytes()
    copied = (out / "adapter_model.safetensors").read_bytes()
    assert original == copied


def test_save_writes_readme(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    p.save(src, out)

    assert (out / "README.md").exists()


def test_save_readme_contains_model_id(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    p.save(src, out)

    readme = (out / "README.md").read_text()
    assert _MODEL_ID in readme


def test_save_readme_contains_base_model(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    p.save(src, out)

    readme = (out / "README.md").read_text()
    assert "Qwen2.5-Coder" in readme


def test_save_files_list_includes_safetensors(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    info = p.save(src, out)

    names = [f.name for f in info.files]
    assert "adapter_model.safetensors" in names


def test_save_files_list_includes_readme(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"

    p = AdapterPackager()
    info = p.save(src, out)

    names = [f.name for f in info.files]
    assert "README.md" in names


def test_save_raises_if_source_missing(tmp_path: Path) -> None:
    p = AdapterPackager()
    with pytest.raises((FileNotFoundError, NotADirectoryError, ValueError)):
        p.save(tmp_path / "nonexistent", tmp_path / "out")


def test_save_raises_if_adapter_config_missing(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path, files={"adapter_model.safetensors": b"\x00"})
    out = tmp_path / "packaged"

    p = AdapterPackager()
    with pytest.raises((FileNotFoundError, ValueError)):
        p.save(src, out)


def test_save_raises_if_safetensors_missing(tmp_path: Path) -> None:
    src = _make_source_dir(
        tmp_path,
        files={
            "adapter_config.json": json.dumps(
                {"base_model_name_or_path": "Qwen/Qwen2.5-Coder-7B-Instruct"}
            ).encode()
        },
    )
    out = tmp_path / "packaged"

    p = AdapterPackager()
    with pytest.raises((FileNotFoundError, ValueError)):
        p.save(src, out)


# ---------------------------------------------------------------------------
# AdapterPackager.upload() -- mocked HuggingFace Hub
# ---------------------------------------------------------------------------


def test_upload_returns_repo_url(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"
    p = AdapterPackager()
    p.save(src, out)

    with patch("finetuning.packager.upload_folder") as mock_upload:
        mock_upload.return_value = MagicMock(repo_url=f"https://huggingface.co/{_MODEL_ID}")
        url = p.upload(out, token="hf_fake_token")

    assert url.startswith("https://huggingface.co/")


def test_upload_calls_upload_folder_with_correct_repo_id(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"
    p = AdapterPackager()
    p.save(src, out)

    with patch("finetuning.packager.upload_folder") as mock_upload:
        mock_upload.return_value = MagicMock(repo_url=f"https://huggingface.co/{_MODEL_ID}")
        p.upload(out, token="hf_fake_token")

    mock_upload.assert_called_once()
    call_kwargs = mock_upload.call_args.kwargs
    assert call_kwargs.get("repo_id") == _MODEL_ID or mock_upload.call_args.args[0] == _MODEL_ID


def test_upload_raises_without_token(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    out = tmp_path / "packaged"
    p = AdapterPackager()
    p.save(src, out)

    with pytest.raises(ValueError, match="token"):
        p.upload(out)
