"""Repository scanner.

Walks a checkout root, applies ``.codereviewer/ignore.txt`` plus a small
default ignore set, classifies each file by purpose, and yields typed
:class:`FileRecord` rows. The chunker in OP-12 consumes those records and
turns them into vector-store chunks.

Classification is intentionally simple. Anything that looks like
documentation goes into the docs bucket so the architecture agent can find
it. Tests go into their own bucket so the test-coverage agent can reason
about coverage gaps without seeing production code as a test reference.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

CODEREVIEWER_DIR = ".codereviewer"

# Patterns that always get skipped regardless of what the user puts in their
# own ignore.txt. Keep this short and obvious. Anything controversial belongs
# in the user-editable file.
_BUILTIN_IGNORES: tuple[str, ...] = (
    ".git/**",
    ".hg/**",
    ".svn/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.pytest_cache/**",
    "**/.tox/**",
    ".venv/**",
    "venv/**",
    "node_modules/**",
    "**/*.pyc",
)

# Suffix -> language name for source classification.
_LANGUAGES_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".cs": "csharp",
}

_DOC_SUFFIXES: frozenset[str] = frozenset({".md", ".mdx", ".rst"})


class FileKind(StrEnum):
    """High-level classification used by retrieval to pick the right bucket."""

    documentation = "documentation"
    source = "source"
    tests = "tests"
    rules = "rules"
    other = "other"


@dataclass(frozen=True)
class FileRecord:
    """One file the scanner is willing to hand off to the chunker."""

    path: Path
    absolute_path: Path
    kind: FileKind
    size_bytes: int
    language: str | None = None


class IgnoreMatcher:
    """Matches paths against a list of gitignore-flavored globs.

    We deliberately do not implement the full ``.gitignore`` spec. OpenRabbit
    only needs simple ``fnmatch`` globs and recursive ``**`` segments, which
    matches what the OP-2 init template ships with.
    """

    def __init__(self, patterns: list[str]) -> None:
        self._patterns = [
            p for p in (line.strip() for line in patterns) if p and not p.startswith("#")
        ]

    @classmethod
    def from_file(
        cls, path: Path, *, defaults: tuple[str, ...] = _BUILTIN_IGNORES
    ) -> IgnoreMatcher:
        loaded: list[str] = list(defaults)
        if path.is_file():
            loaded.extend(path.read_text(encoding="utf-8").splitlines())
        return cls(loaded)

    @property
    def patterns(self) -> list[str]:
        return list(self._patterns)

    def matches(self, relative_posix: str) -> bool:
        return any(_matches(relative_posix, pattern) for pattern in self._patterns)


def _matches(path: str, pattern: str) -> bool:
    """Match ``path`` against a single gitignore-flavored glob.

    ``**`` matches any number of path segments, including zero. A pattern
    without a slash matches the basename. A pattern ending with ``/`` matches
    directories only, but since :class:`RepositoryScanner` calls us with file
    paths the trailing slash is treated as a regular glob anchor.
    """
    if "/" not in pattern.strip("/"):
        return fnmatch.fnmatch(Path(path).name, pattern)
    pattern = pattern.lstrip("/")
    # fnmatch already understands ``**``-style matching when we feed it the
    # full string. Convert ``**/`` to ``*`` segments to keep behavior simple.
    if pattern.endswith("/"):
        pattern = pattern + "**"
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern + "/**")


@dataclass(frozen=True)
class RepositoryScanner:
    """Yields :class:`FileRecord` instances for every reviewable file in a repo."""

    include_other: bool = False
    """When False (default), files classified as ``other`` are dropped."""

    def scan(self, root: Path) -> Iterator[FileRecord]:
        root = root.resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)

        matcher = IgnoreMatcher.from_file(root / CODEREVIEWER_DIR / "ignore.txt")
        for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            posix = relative.as_posix()
            if matcher.matches(posix):
                continue
            kind = _classify(relative)
            if kind is FileKind.other and not self.include_other:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            yield FileRecord(
                path=relative,
                absolute_path=path,
                kind=kind,
                size_bytes=size,
                language=_LANGUAGES_BY_SUFFIX.get(relative.suffix.lower()),
            )


def _classify(relative: Path) -> FileKind:
    parts = relative.parts
    suffix = relative.suffix.lower()

    if parts and parts[0] == CODEREVIEWER_DIR:
        return FileKind.rules

    if _is_test_path(relative):
        return FileKind.tests

    if suffix in _DOC_SUFFIXES:
        return FileKind.documentation

    if parts and parts[0] == "docs":
        return FileKind.documentation

    if suffix in _LANGUAGES_BY_SUFFIX:
        return FileKind.source

    return FileKind.other


def _is_test_path(relative: Path) -> bool:
    parts = relative.parts
    if any(part == "tests" or part == "test" for part in parts):
        return True
    name = relative.name
    if name.startswith("test_") and relative.suffix == ".py":
        return True
    if name.endswith("_test.py"):
        return True
    if name.endswith((".test.ts", ".test.tsx")):
        return True
    if name.endswith((".spec.ts", ".spec.tsx")):
        return True
    return name.endswith((".test.js", ".spec.js"))
