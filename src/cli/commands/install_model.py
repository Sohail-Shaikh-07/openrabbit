"""Implementation of the ``openrabbit install-model`` command.

Downloads the OpenRabbit-Reviewer-v1 LoRA adapter from HuggingFace Hub
to a local directory and verifies the expected files are present.

Kept separate from ``cli.main`` so it can be unit-tested without going
through the Typer CLI runner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ID = "openrabbit/openrabbit-reviewer-v1"
_DEFAULT_INSTALL_ROOT = Path.home() / ".openrabbit" / "models"
_REQUIRED_FILES = ("adapter_model.safetensors", "adapter_config.json")


@dataclass(frozen=True)
class InstallResult:
    """Result of a successful model installation.

    Attributes
    ----------
    model_id:
        HuggingFace Hub repo ID that was downloaded.
    install_dir:
        Local directory where the adapter files are stored.
    """

    model_id: str
    install_dir: Path


def run_install_model(
    model_id: str = _DEFAULT_MODEL_ID,
    install_dir: Path | None = None,
    token: str | None = None,
) -> InstallResult:
    """Download and install a LoRA adapter from HuggingFace Hub.

    Parameters
    ----------
    model_id:
        HuggingFace Hub repo ID to download. Defaults to
        ``openrabbit/openrabbit-reviewer-v1``.
    install_dir:
        Directory where the adapter is saved. Defaults to
        ``~/.openrabbit/models/<model-name>/``.
    token:
        Optional HuggingFace Hub token for private repos.

    Returns
    -------
    InstallResult
        Metadata about the installed adapter.

    Raises
    ------
    FileNotFoundError
        If required adapter files are absent after download.
    RuntimeError
        If the download fails.
    """
    model_name = model_id.split("/")[-1]
    dest = (install_dir or _DEFAULT_INSTALL_ROOT) / model_name
    dest.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading %s to %s ...", model_id, dest)

    snapshot_download(
        repo_id=model_id,
        local_dir=dest,
        token=token,
    )

    _verify(dest)

    logger.info("Installed %s to %s", model_id, dest)
    return InstallResult(model_id=model_id, install_dir=dest)


def _verify(install_dir: Path) -> None:
    """Raise if required adapter files are missing after download."""
    for name in _REQUIRED_FILES:
        if not (install_dir / name).exists():
            raise FileNotFoundError(
                f"Expected adapter file not found after download: {install_dir / name}\n"
                "The HuggingFace repo may be missing required files."
            )
