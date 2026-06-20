"""Embedding pipeline for the OpenRabbit RAG system.

Converts :class:`~rag.chunker.Chunk` objects into dense float vectors using
``BAAI/bge-small-en-v1.5`` via fastembed (ONNX runtime). The model runs fully
locally -- no remote API is called at encode time and no GPU is required.

fastembed is preferred over sentence-transformers because it uses ONNX rather
than PyTorch, making the installed footprint roughly 100 MB instead of ~1.7 GB.

Usage::

    from rag.embeddings import EmbeddingEngine

    engine = EmbeddingEngine()
    embedded = engine.encode(chunks)          # sync
    embedded = await engine.aencode(chunks)   # async wrapper
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from rag.chunker import Chunk

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_DEFAULT_BATCH_SIZE = 32


@dataclass
class EmbeddedChunk:
    """A :class:`~rag.chunker.Chunk` paired with its embedding vector.

    Attributes
    ----------
    chunk:
        The original chunk produced by the chunking engine.
    vector:
        Unit-normalised float32 embedding (384 dimensions for BGE-Small).
    """

    chunk: Chunk
    vector: np.ndarray


class EmbeddingEngine:
    """Encodes :class:`~rag.chunker.Chunk` objects into dense vectors.

    The underlying fastembed model is loaded lazily on the first call to
    :meth:`encode`, so the constructor is instant even if the model weights
    are not yet cached on disk.

    Parameters
    ----------
    model_name:
        fastembed model identifier. Defaults to ``BAAI/bge-small-en-v1.5``.
    batch_size:
        Number of chunk texts sent to the model in one forward pass. Tune
        downward on machines with limited memory.
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._model: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        """Return an :class:`EmbeddedChunk` for every chunk in *chunks*.

        Vectors are L2-normalised so that downstream cosine similarity
        comparisons reduce to a simple dot product.

        Raises
        ------
        RuntimeError
            If the model fails to initialise (propagates from fastembed).
        """
        if not chunks:
            return []

        model = self._load_model()
        texts = [c.text for c in chunks]
        vectors = self._encode_batched(model, texts)
        return [
            EmbeddedChunk(chunk=chunk, vector=vec)
            for chunk, vec in zip(chunks, vectors, strict=False)
        ]

    async def aencode(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        """Async wrapper around :meth:`encode`.

        Runs :meth:`encode` in the default executor so it does not block the
        event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.encode, chunks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_model(self) -> Any:
        """Load (or return cached) the fastembed TextEmbedding model."""
        if self._model is None:
            fe = importlib.import_module("fastembed")
            logger.info("loading embedding model %s", self._model_name)
            self._model = fe.TextEmbedding(
                model_name=self._model_name,
                max_length=512,
            )
        return self._model

    def _encode_batched(self, model: Any, texts: list[str]) -> list[np.ndarray]:
        """Encode *texts* in batches and return L2-normalised float32 vectors."""
        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            # fastembed.TextEmbedding.embed() returns a generator of ndarrays.
            batch_vecs = list(model.embed(batch, batch_size=len(batch)))
            arr = np.array(batch_vecs, dtype="float32")
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            normalised = arr / norms
            for vec in normalised:
                vectors.append(vec)
        return vectors
