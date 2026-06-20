"""Chunking engine for the OpenRabbit RAG pipeline.

Converts raw :class:`~rag.scanner.FileRecord` objects into vector-store-ready
:class:`Chunk` instances. Three strategies are implemented:

* **AST chunking** (Python, JavaScript, TypeScript) -- top-level functions and
  classes are extracted via Tree-sitter. Each becomes its own chunk so that
  retrieval returns semantically complete units of code.

* **Section chunking** (Markdown) -- the text is split on ATX headings
  (``#``, ``##``, ...). Sections that exceed the window size are further split
  with a configurable overlap so that no context is lost at section boundaries.

* **Pass-through** for documentation file kinds that are not Markdown and for
  rules files: they fall through to section chunking since the content is
  almost always structured text.

Files classified as :attr:`~rag.scanner.FileKind.other` are returned as an
empty list; the retriever has no use for binary or lock files.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from tree_sitter import Node, Parser

from rag.scanner import FileKind, FileRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

_CHUNK_WINDOW = 1000
_CHUNK_OVERLAP = 150

# Language names used by tree-sitter-language-pack.
_LANG_PYTHON = "python"
_LANG_JS = "javascript"
_LANG_TS = "typescript"

_AST_LANGUAGES: frozenset[str] = frozenset({_LANG_PYTHON, _LANG_JS, _LANG_TS})


class ChunkKind(StrEnum):
    """Classification for a :class:`Chunk`."""

    function = "function"
    klass = "class"
    section = "section"


@dataclass(frozen=True)
class Chunk:
    """One unit of content ready to be embedded.

    Attributes
    ----------
    source_path:
        Repository-relative path of the file this chunk came from.
    kind:
        Whether this is a function, class, or prose section.
    name:
        Human-readable identifier (function/class name or heading text).
    text:
        The actual source text to embed.
    language:
        Language tag (``"python"``, ``"javascript"``, ``"typescript"``, or
        ``None`` for prose).
    byte_span:
        ``(start, end)`` byte offsets within the original file content.
        For markdown sections this is an approximation based on character
        position rather than a precise byte offset.
    """

    source_path: Path
    kind: ChunkKind
    name: str
    text: str
    language: str | None = None
    byte_span: tuple[int, int] = field(default_factory=lambda: (0, 0))


# ---------------------------------------------------------------------------
# Main chunker
# ---------------------------------------------------------------------------


class Chunker:
    """Converts a :class:`~rag.scanner.FileRecord` into a list of :class:`Chunk` objects.

    Usage::

        from rag.chunker import Chunker
        from rag.scanner import RepositoryScanner

        scanner = RepositoryScanner()
        chunker = Chunker()
        for record in scanner.scan(repo_root):
            for chunk in chunker.chunk(record):
                ...  # embed and store
    """

    def chunk(self, record: FileRecord) -> list[Chunk]:
        """Return chunks for *record*.

        Returns an empty list when the file kind is :attr:`FileKind.other` or
        when the file cannot be read.
        """
        if record.kind is FileKind.other:
            return []

        try:
            text = record.absolute_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("cannot read %s", record.absolute_path)
            return []

        if not text.strip():
            return []

        # Source files with a supported language get AST chunking.
        if record.kind in (FileKind.source, FileKind.tests) and record.language in _AST_LANGUAGES:
            return _ast_chunks(text, record)

        # Documentation, rules, and everything else falls back to section chunking.
        suffix = record.path.suffix.lower()
        if suffix in {".md", ".mdx", ".rst"}:
            return _markdown_chunks(text, record)

        # Non-markdown doc files get a single section chunk.
        return [
            Chunk(
                source_path=record.path,
                kind=ChunkKind.section,
                name=record.path.name,
                text=text,
                language=record.language,
                byte_span=(0, len(text.encode())),
            )
        ]


# ---------------------------------------------------------------------------
# AST chunking (Python / JS / TS)
# ---------------------------------------------------------------------------

# Node type names that map to ChunkKind.function
_FUNCTION_NODE_TYPES: frozenset[str] = frozenset(
    {
        # Python
        "function_definition",
        # JavaScript / TypeScript
        "function_declaration",
        "method_definition",
        # Arrow functions assigned to a variable (lexical_declaration or
        # expression_statement wrapping an assignment_expression).
        # We handle these via the parent walk below.
    }
)

_CLASS_NODE_TYPES: frozenset[str] = frozenset(
    {
        "class_definition",  # Python
        "class_declaration",  # JS / TS
    }
)

# For arrow functions: variable_declarator -> arrow_function
_ARROW_FUNCTION_TYPE = "arrow_function"
_VARIABLE_DECLARATOR_TYPE = "variable_declarator"
_LEXICAL_DECLARATION_TYPE = "lexical_declaration"


def _get_parser(language: str) -> Parser:
    """Return a Tree-sitter parser for *language*."""
    from tree_sitter_language_pack import get_parser

    return get_parser(language)  # type: ignore[arg-type]


def _node_name(node: Node, source_bytes: bytes) -> str:
    """Extract a human-readable name from a Tree-sitter node."""
    # Python uses ``identifier``, JS uses ``identifier`` or ``property_identifier``,
    # TypeScript uses ``type_identifier`` for class names.
    _NAME_TYPES = ("identifier", "property_identifier", "type_identifier")
    for child in node.children:
        if child.type in _NAME_TYPES:
            return source_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return "<anonymous>"


def _ast_chunks(text: str, record: FileRecord) -> list[Chunk]:
    """Parse *text* with Tree-sitter and return one chunk per top-level definition."""
    assert record.language is not None
    try:
        parser = _get_parser(record.language)
    except Exception:
        logger.exception("failed to load Tree-sitter parser for %s", record.language)
        return []

    source_bytes = text.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    chunks: list[Chunk] = []
    _walk_top_level(root, source_bytes, record, chunks)
    return chunks


def _walk_top_level(node: Node, source_bytes: bytes, record: FileRecord, out: list[Chunk]) -> None:
    """Walk direct children of *node* and collect function/class chunks."""
    for child in node.children:
        node_type: str = child.type

        if node_type in _FUNCTION_NODE_TYPES:
            name = _node_name(child, source_bytes)
            out.append(_make_code_chunk(child, name, ChunkKind.function, source_bytes, record))
            continue

        if node_type in _CLASS_NODE_TYPES:
            name = _node_name(child, source_bytes)
            out.append(_make_code_chunk(child, name, ChunkKind.klass, source_bytes, record))
            continue

        # Arrow functions: const/let/var identifier = (...) => ...
        if node_type == _LEXICAL_DECLARATION_TYPE:
            _handle_lexical_declaration(child, source_bytes, record, out)
            continue

        # expression_statement containing assignment_expression (var = () => {})
        if node_type == "expression_statement":
            _handle_expression_statement(child, source_bytes, record, out)


def _handle_lexical_declaration(
    node: Node, source_bytes: bytes, record: FileRecord, out: list[Chunk]
) -> None:
    """Extract arrow functions from ``const/let/var name = () => ...``."""
    for child in node.children:
        if child.type == _VARIABLE_DECLARATOR_TYPE:
            name = _node_name(child, source_bytes)
            for sub in child.children:
                if sub.type == _ARROW_FUNCTION_TYPE:
                    out.append(
                        _make_code_chunk(node, name, ChunkKind.function, source_bytes, record)
                    )
                    return


def _handle_expression_statement(
    node: Node, source_bytes: bytes, record: FileRecord, out: list[Chunk]
) -> None:
    """Extract arrow functions from expression statements."""
    for child in node.children:
        if child.type == "assignment_expression":
            name_node: Node | None = None
            has_arrow = False
            for sub in child.children:
                if sub.type == "identifier" and name_node is None:
                    name_node = sub
                if sub.type == _ARROW_FUNCTION_TYPE:
                    has_arrow = True
            if has_arrow and name_node is not None:
                name = source_bytes[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                out.append(_make_code_chunk(node, name, ChunkKind.function, source_bytes, record))


def _make_code_chunk(
    node: Node,
    name: str,
    kind: ChunkKind,
    source_bytes: bytes,
    record: FileRecord,
) -> Chunk:
    start: int = node.start_byte
    end: int = node.end_byte
    text = source_bytes[start:end].decode("utf-8", errors="replace")
    return Chunk(
        source_path=record.path,
        kind=kind,
        name=name,
        text=text,
        language=record.language,
        byte_span=(start, end),
    )


# ---------------------------------------------------------------------------
# Markdown section chunking
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)


def _markdown_chunks(text: str, record: FileRecord) -> list[Chunk]:
    """Split Markdown text on ATX headings.

    Sections longer than ``_CHUNK_WINDOW`` characters are further split with
    ``_CHUNK_OVERLAP`` character overlap so that no context is lost at the
    split boundary.
    """
    boundaries = [m for m in _HEADING_RE.finditer(text)]

    if not boundaries:
        # No headings: whole document is one section.
        return _split_section(text, "document", 0, record)

    chunks: list[Chunk] = []
    for i, match in enumerate(boundaries):
        heading_text = match.group(2).strip()
        start = match.start()
        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(text)
        section_text = text[start:end]
        chunks.extend(_split_section(section_text, heading_text, start, record))

    return chunks


def _split_section(text: str, name: str, byte_offset: int, record: FileRecord) -> list[Chunk]:
    """Return one or more :class:`Chunk` objects for a single section.

    Sections within the window size are returned as-is. Larger sections are
    split with overlap.
    """
    if len(text) <= _CHUNK_WINDOW:
        if not text.strip():
            return []
        return [
            Chunk(
                source_path=record.path,
                kind=ChunkKind.section,
                name=name,
                text=text,
                language=None,
                byte_span=(byte_offset, byte_offset + len(text.encode())),
            )
        ]

    # Split with overlap.
    chunks: list[Chunk] = []
    pos = 0
    while pos < len(text):
        window = text[pos : pos + _CHUNK_WINDOW]
        if not window.strip():
            break
        chunk_start = byte_offset + len(text[:pos].encode())
        chunk_end = chunk_start + len(window.encode())
        chunks.append(
            Chunk(
                source_path=record.path,
                kind=ChunkKind.section,
                name=name,
                text=window,
                language=None,
                byte_span=(chunk_start, chunk_end),
            )
        )
        pos += _CHUNK_WINDOW - _CHUNK_OVERLAP
        if pos + _CHUNK_OVERLAP >= len(text):
            break

    return chunks
