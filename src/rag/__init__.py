"""Repository-aware retrieval-augmented generation pipeline (Phase 3)."""

from __future__ import annotations

from rag.chunker import Chunk, Chunker, ChunkKind
from rag.embeddings import EmbeddedChunk, EmbeddingEngine
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
    "EmbeddedChunk",
    "EmbeddingEngine",
    "FileKind",
    "FileRecord",
    "IgnoreMatcher",
    "RepositoryScanner",
]
