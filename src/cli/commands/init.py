"""Implementation of ``openrabbit init``.

Creates the ``.codereviewer/`` scaffold in a target directory. Pure function on
the filesystem so it can be tested with ``tmp_path`` without invoking the full
Typer app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cli.logging import get_logger
from cli.templates import TEMPLATES

_log = get_logger(__name__)


SCAFFOLD_DIR_NAME = ".codereviewer"


@dataclass(frozen=True)
class InitResult:
    """Outcome of a single ``init`` invocation."""

    scaffold_dir: Path
    created: list[Path] = field(default_factory=list)
    overwritten: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


class InitConflict(RuntimeError):
    """Raised when an existing file would be overwritten without ``--force``."""

    def __init__(self, conflicts: list[Path]) -> None:
        self.conflicts = conflicts
        joined = ", ".join(str(p) for p in conflicts)
        super().__init__(f"refusing to overwrite existing files: {joined}")


def run_init(target_dir: Path, *, force: bool = False) -> InitResult:
    """Create ``<target_dir>/.codereviewer/`` and write template files.

    Args:
        target_dir: Repository root where the scaffold should live.
        force: When true, existing scaffold files are overwritten.

    Returns:
        An :class:`InitResult` summarising filesystem effects.

    Raises:
        InitConflict: If ``force`` is false and any template file already exists.
        FileNotFoundError: If ``target_dir`` does not exist.
        NotADirectoryError: If ``target_dir`` is not a directory.
    """
    if not target_dir.exists():
        raise FileNotFoundError(target_dir)
    if not target_dir.is_dir():
        raise NotADirectoryError(target_dir)

    scaffold = target_dir / SCAFFOLD_DIR_NAME
    scaffold.mkdir(parents=True, exist_ok=True)

    paths = {name: scaffold / name for name in TEMPLATES}

    if not force:
        existing = [p for p in paths.values() if p.exists()]
        if existing:
            raise InitConflict(existing)

    result = InitResult(scaffold_dir=scaffold)
    for name, content in TEMPLATES.items():
        path = paths[name]
        existed = path.exists()
        path.write_text(content, encoding="utf-8")
        if existed:
            result.overwritten.append(path)
        else:
            result.created.append(path)

    _log.info(
        "init.complete",
        scaffold=str(scaffold),
        created=len(result.created),
        overwritten=len(result.overwritten),
    )
    return result
