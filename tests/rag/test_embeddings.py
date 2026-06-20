"""Tests for ``rag.embeddings``.

The sentence-transformers model is mocked so tests run in CI without a GPU
or the 130 MB model download.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rag.chunker import Chunk, ChunkKind
from rag.embeddings import EmbeddedChunk, EmbeddingEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_chunk(name: str = "foo", text: str = "def foo(): pass") -> Chunk:
    return Chunk(
        source_path=Path("src/foo.py"),
        kind=ChunkKind.function,
        name=name,
        text=text,
        language="python",
        byte_span=(0, len(text.encode())),
    )


def _mock_model(dim: int = 384) -> MagicMock:
    """Return a mock fastembed TextEmbedding that yields constant-value embeddings."""
    import numpy as np

    model = MagicMock()

    def embed_fn(texts: list[str], **kwargs: object) -> object:
        return iter([np.ones(dim, dtype="float32") for _ in texts])

    model.embed.side_effect = embed_fn
    return model


# ---------------------------------------------------------------------------
# EmbeddedChunk
# ---------------------------------------------------------------------------


def test_embedded_chunk_stores_chunk_and_vector() -> None:
    import numpy as np

    chunk = _make_chunk()
    vec = np.array([0.1, 0.2, 0.3], dtype="float32")
    ec = EmbeddedChunk(chunk=chunk, vector=vec)

    assert ec.chunk is chunk
    assert ec.vector is vec


# ---------------------------------------------------------------------------
# EmbeddingEngine.encode
# ---------------------------------------------------------------------------


def test_encode_returns_one_embedded_chunk_per_input() -> None:
    engine = EmbeddingEngine()
    chunks = [_make_chunk("a"), _make_chunk("b"), _make_chunk("c")]

    with patch.object(engine, "_model", _mock_model()):
        results = engine.encode(chunks)

    assert len(results) == 3
    assert all(isinstance(r, EmbeddedChunk) for r in results)


def test_encode_vector_has_correct_dimension() -> None:
    engine = EmbeddingEngine()
    chunk = _make_chunk()

    with patch.object(engine, "_model", _mock_model(384)):
        results = engine.encode([chunk])

    assert results[0].vector.shape == (384,)


def test_encode_chunk_reference_is_preserved() -> None:
    engine = EmbeddingEngine()
    chunk = _make_chunk("my_func", "def my_func(): return 42")

    with patch.object(engine, "_model", _mock_model()):
        results = engine.encode([chunk])

    assert results[0].chunk is chunk


def test_encode_empty_list_returns_empty() -> None:
    engine = EmbeddingEngine()
    with patch.object(engine, "_model", _mock_model()):
        results = engine.encode([])

    assert results == []


def test_encode_embeddings_are_normalised() -> None:
    import numpy as np

    engine = EmbeddingEngine()
    chunk = _make_chunk()

    with patch.object(engine, "_model", _mock_model()):
        results = engine.encode([chunk])

    vec = results[0].vector
    norm = float(np.linalg.norm(vec))
    assert abs(norm - 1.0) < 1e-5, f"expected unit vector, got norm={norm}"


def test_encode_batches_correctly() -> None:
    engine = EmbeddingEngine(batch_size=2)
    chunks = [_make_chunk(f"f{i}") for i in range(5)]

    call_sizes: list[int] = []
    import numpy as np

    model = MagicMock()

    def embed_fn(texts: list[str], **kwargs: object) -> object:
        call_sizes.append(len(texts))
        return iter([np.ones(384, dtype="float32") for _ in texts])

    model.embed.side_effect = embed_fn

    with patch.object(engine, "_model", model):
        results = engine.encode(chunks)

    assert len(results) == 5
    # With batch_size=2 and 5 chunks: batches of [2, 2, 1]
    assert call_sizes == [2, 2, 1]


def test_encode_passes_text_to_model() -> None:
    engine = EmbeddingEngine()
    chunk = _make_chunk("greet", "def greet(): return 'hello'")

    captured: list[list[str]] = []
    import numpy as np

    model = MagicMock()

    def embed_fn(texts: list[str], **kwargs: object) -> object:
        captured.append(list(texts))
        return iter([np.ones(384, dtype="float32") for _ in texts])

    model.embed.side_effect = embed_fn

    with patch.object(engine, "_model", model):
        engine.encode([chunk])

    assert captured[0][0] == chunk.text


# ---------------------------------------------------------------------------
# EmbeddingEngine.aencode (async wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aencode_returns_same_results_as_encode() -> None:
    engine = EmbeddingEngine()
    chunk = _make_chunk()

    with patch.object(engine, "_model", _mock_model()):
        sync_results = engine.encode([chunk])
        async_results = await engine.aencode([chunk])

    assert len(async_results) == len(sync_results)
    assert async_results[0].chunk is sync_results[0].chunk


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def test_model_loads_lazily_on_first_encode() -> None:
    engine = EmbeddingEngine()
    assert engine._model is None

    fake_model = _mock_model()
    with patch("fastembed.TextEmbedding", return_value=fake_model) as mock_cls:
        engine.encode([_make_chunk()])
        mock_cls.assert_called_once()
        assert engine._model is fake_model


def test_model_is_not_reloaded_on_second_call() -> None:
    engine = EmbeddingEngine()

    fake_model = _mock_model()
    with patch("fastembed.TextEmbedding", return_value=fake_model) as mock_cls:
        engine.encode([_make_chunk()])
        engine.encode([_make_chunk()])
        mock_cls.assert_called_once()


def test_model_load_failure_raises_runtime_error() -> None:
    engine = EmbeddingEngine()
    with (
        patch("fastembed.TextEmbedding", side_effect=RuntimeError("no model")),
        pytest.raises(RuntimeError, match="no model"),
    ):
        engine.encode([_make_chunk()])
