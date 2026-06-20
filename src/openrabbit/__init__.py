"""OpenRabbit: open-source, self-hosted AI Pull Request Review platform.

Top-level package. Submodules are organized by concern:

- ``openrabbit.cli``        : Typer-based command-line interface.
- ``openrabbit.api``        : FastAPI HTTP surface used by the local daemon.
- ``openrabbit.configs``    : Configuration loading, schemas, defaults.
- ``openrabbit.github``     : GitHub REST/GraphQL clients, polling, PR parsing.
- ``openrabbit.rag``        : Repository scanner, chunker, embedder, retriever.
- ``openrabbit.agents``     : Multi-agent review system (LangGraph).
- ``openrabbit.ranking``    : Comment dedupe / ranking / noise reduction.
- ``openrabbit.models``     : Model serving abstractions (Ollama, vLLM, HF).
- ``openrabbit.finetuning`` : QLoRA training pipeline + dataset prep.

The ``__version__`` value is the single source of truth for the package version
and is read by setuptools/poetry tooling as well as the CLI ``--version`` flag.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
