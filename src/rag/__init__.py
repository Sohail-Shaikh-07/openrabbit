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
from rag.vector_store import (
    ALL_COLLECTIONS,
    COLLECTION_CLASSES,
    COLLECTION_DOCS,
    COLLECTION_FUNCTIONS,
    COLLECTION_REVIEWS,
    COLLECTION_RULES,
    VECTOR_SIZE,
    VectorStore,
)

__all__ = [
    "ALL_COLLECTIONS",
    "CODEREVIEWER_DIR",
    "COLLECTION_CLASSES",
    "COLLECTION_DOCS",
    "COLLECTION_FUNCTIONS",
    "COLLECTION_REVIEWS",
    "COLLECTION_RULES",
    "VECTOR_SIZE",
    "Chunk",
    "ChunkKind",
    "Chunker",
    "EmbeddedChunk",
    "EmbeddingEngine",
    "FileKind",
    "FileRecord",
    "IgnoreMatcher",
    "RepositoryScanner",
    "VectorStore",
]
