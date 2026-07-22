"""Explicit multi-repository knowledge connector."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from configs.schema import MultiRepoConnectorSettings, MultiRepoReferenceSettings
from knowledge.connectors import (
    KnowledgeConnectorHealth,
    KnowledgeConnectorRequest,
    KnowledgeItem,
    KnowledgeSourceKind,
    normalize_knowledge_items,
    sanitize_knowledge_text,
)
from rag.chunker import Chunk, Chunker
from rag.scanner import FileRecord, RepositoryScanner

_MAX_SCAN_FILES_PER_REPO = 200
_MAX_CHUNKS_PER_REPO = 300
_MAX_FILE_BYTES = 80_000
_DEFAULT_MAX_BODY_CHARS = 1200
_GENERATED_PARTS = frozenset(
    {
        "build",
        "dist",
        "coverage",
        "vendor",
        "target",
        "out",
        "generated",
        ".next",
        ".turbo",
    }
)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./-]{3,}")


@dataclass(frozen=True)
class _ResolvedRepository:
    reference: MultiRepoReferenceSettings
    display_name: str
    repo_handle: str
    root: Path | None


class MultiRepoConnector:
    """Optional local multi-repo context connector.

    The connector only reads paths that are explicitly configured. Repository
    handles without local paths are treated as allowed identifiers for future
    integrations, but they do not trigger cloning or network access.
    """

    name = "multi_repo"
    source_kind = KnowledgeSourceKind.MULTI_REPO

    def __init__(
        self,
        settings: MultiRepoConnectorSettings,
        *,
        workspace_root: Path | None = None,
        scanner: RepositoryScanner | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        self._settings = settings
        self._workspace_root = workspace_root.resolve() if workspace_root else Path.cwd()
        self._scanner = scanner or RepositoryScanner()
        self._chunker = chunker or Chunker()

    def is_available(self) -> KnowledgeConnectorHealth:
        """Return local multi-repo availability without scanning files."""
        if not self._settings.enabled:
            return _health(False, "disabled")
        if not self._settings.repositories:
            return _health(False, "no repositories configured")
        resolved = list(self._resolve_repositories())
        if any(item.root is None for item in resolved):
            return _health(True, "configured")
        if any(item.root is not None and item.root.is_dir() for item in resolved):
            return _health(True, "configured")
        return _health(False, "no configured repository paths are readable")

    def retrieve(self, request: KnowledgeConnectorRequest) -> Sequence[KnowledgeItem]:
        """Return bounded snippets from explicitly configured local repositories."""
        if not self._settings.enabled or not self._settings.repositories:
            return []
        max_items = min(request.max_items, self._settings.max_items)
        terms = _query_terms(request)
        items: list[KnowledgeItem] = []
        for resolved in self._resolve_repositories():
            if resolved.root is None or not resolved.root.is_dir():
                continue
            items.extend(
                self._repository_items(
                    resolved,
                    request=request,
                    terms=terms,
                    max_items=max_items,
                )
            )
            if len(items) >= max_items * 4:
                break
        return normalize_knowledge_items(
            items,
            max_items=max_items,
            max_body_chars=_DEFAULT_MAX_BODY_CHARS,
        )

    def _repository_items(
        self,
        resolved: _ResolvedRepository,
        *,
        request: KnowledgeConnectorRequest,
        terms: frozenset[str],
        max_items: int,
    ) -> list[KnowledgeItem]:
        assert resolved.root is not None
        items: list[KnowledgeItem] = []
        file_count = 0
        chunk_count = 0
        try:
            records = self._scanner.scan(resolved.root)
        except (OSError, NotADirectoryError):
            return []
        for record in records:
            if not _record_allowed(record):
                continue
            file_count += 1
            if file_count > _MAX_SCAN_FILES_PER_REPO:
                break
            for chunk in self._chunker.chunk(record):
                chunk_count += 1
                if chunk_count > _MAX_CHUNKS_PER_REPO:
                    return items
                score = _score_chunk(chunk, terms, request)
                if score <= 0.0 and terms:
                    continue
                items.append(_knowledge_item(resolved, chunk, score=score))
                if len(items) >= max_items * 3:
                    return items
        return items

    def _resolve_repositories(self) -> Iterable[_ResolvedRepository]:
        for reference in self._settings.repositories:
            root = _resolve_path(reference.path, base=self._workspace_root)
            repo_handle = reference.repo or ""
            display_name = reference.name or repo_handle or (root.name if root else "repository")
            yield _ResolvedRepository(
                reference=reference,
                display_name=display_name,
                repo_handle=repo_handle,
                root=root,
            )


def _health(available: bool, reason: str) -> KnowledgeConnectorHealth:
    return KnowledgeConnectorHealth(
        name="multi_repo",
        source_kind=KnowledgeSourceKind.MULTI_REPO,
        available=available,
        reason=reason,
    )


def _resolve_path(path_value: str | None, *, base: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _record_allowed(record: FileRecord) -> bool:
    if record.size_bytes > _MAX_FILE_BYTES:
        return False
    parts = record.path.parts
    if any(part.startswith(".") for part in parts):
        return False
    return not any(part.lower() in _GENERATED_PARTS for part in parts)


def _query_terms(request: KnowledgeConnectorRequest) -> frozenset[str]:
    values = [
        request.query,
        " ".join(request.changed_paths),
        " ".join(request.changed_symbols),
        " ".join(request.metadata.values()),
    ]
    terms: set[str] = set()
    for value in values:
        terms.update(match.group(0).lower() for match in _TOKEN_PATTERN.finditer(value))
    return frozenset(term for term in terms if len(term) >= 3)


def _score_chunk(
    chunk: Chunk,
    terms: frozenset[str],
    request: KnowledgeConnectorRequest,
) -> float:
    haystack = " ".join(
        [
            chunk.source_path.as_posix(),
            chunk.name,
            chunk.text[:2000],
            " ".join(chunk.metadata.values()),
        ]
    ).lower()
    if not terms:
        return 0.4
    matches = sum(1 for term in terms if term in haystack)
    changed_names = {Path(path).name.lower() for path in request.changed_paths}
    if chunk.source_path.name.lower() in changed_names:
        matches += 2
    if matches <= 0:
        return 0.0
    return min(1.0, 0.35 + (matches * 0.12))


def _knowledge_item(
    resolved: _ResolvedRepository,
    chunk: Chunk,
    *,
    score: float,
) -> KnowledgeItem:
    path = chunk.source_path.as_posix()
    title = f"{resolved.display_name}: {chunk.name} ({path})"
    return KnowledgeItem(
        source_id=f"multi_repo:{_source_suffix(resolved.display_name, path, chunk.name)}",
        source_kind=KnowledgeSourceKind.MULTI_REPO,
        title=title,
        body=chunk.text,
        repo=resolved.repo_handle or resolved.display_name,
        path=path,
        score=score,
        metadata={
            "source": "local_path",
            "repo": resolved.display_name,
            "repo_handle": resolved.repo_handle,
            "path": path,
            "kind": chunk.kind.value,
            "trust": "untrusted",
        },
    )


def _source_suffix(*parts: str) -> str:
    text = ":".join(sanitize_knowledge_text(part, max_chars=180) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
