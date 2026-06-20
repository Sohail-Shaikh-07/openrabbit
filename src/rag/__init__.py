"""Repository-aware retrieval-augmented generation pipeline (Phase 3)."""

from __future__ import annotations

from rag.chunker import Chunk, ChunkKind, Chunker
from rag.scanner import (
    CODEREVIEWER_DIR,
    FileKind,
    FileRecord,
    IgnoreMatcher,
    RepositoryScanner,
)

__all__ = [
    "CODEREVIEWER_DIR",
    "Chunk",
    "ChunkKind",
    "Chunker",
    "FileKind",
    "FileRecord",
    "IgnoreMatcher",
    "RepositoryScanner",
]
