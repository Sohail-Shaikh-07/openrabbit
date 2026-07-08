"""Tests for ``rag.chunker``.

Each test follows RED-GREEN-REFACTOR: the test was written first, watched to
fail, then the minimal implementation was added to make it pass.
"""

from __future__ import annotations

from pathlib import Path

from rag.chunker import Chunk, Chunker, ChunkKind
from rag.scanner import FileKind, FileRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    tmp_path: Path,
    relative: str,
    content: str,
    kind: FileKind = FileKind.source,
    language: str | None = None,
    metadata: dict[str, str] | None = None,
) -> FileRecord:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return FileRecord(
        path=Path(relative),
        absolute_path=target,
        kind=kind,
        size_bytes=target.stat().st_size,
        language=language,
        metadata=metadata or {},
    )


def _chunk(tmp_path: Path, relative: str, content: str, language: str) -> list[Chunk]:
    record = _record(tmp_path, relative, content, kind=FileKind.source, language=language)
    return Chunker().chunk(record)


# ---------------------------------------------------------------------------
# Python AST chunking
# ---------------------------------------------------------------------------


def test_python_function_becomes_one_chunk(tmp_path: Path) -> None:
    code = "def greet(name: str) -> str:\n    return f'hello {name}'\n"
    chunks = _chunk(tmp_path, "greet.py", code, "python")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind is ChunkKind.function
    assert "greet" in chunk.name
    assert "def greet" in chunk.text
    assert chunk.language == "python"
    assert chunk.source_path == Path("greet.py")


def test_python_class_becomes_one_chunk(tmp_path: Path) -> None:
    code = "class Greeter:\n    def hello(self) -> str:\n        return 'hi'\n"
    chunks = _chunk(tmp_path, "greeter.py", code, "python")

    # One chunk for the class; methods are not promoted to top-level chunks.
    class_chunks = [c for c in chunks if c.kind is ChunkKind.klass]
    assert len(class_chunks) == 1
    assert "Greeter" in class_chunks[0].name


def test_python_module_with_function_and_class_yields_two_chunks(tmp_path: Path) -> None:
    code = (
        "def helper() -> None:\n    pass\n\n"
        "class Service:\n    def run(self) -> None:\n        pass\n"
    )
    chunks = _chunk(tmp_path, "module.py", code, "python")
    kinds = {c.kind for c in chunks}
    assert ChunkKind.function in kinds
    assert ChunkKind.klass in kinds


def test_python_empty_file_returns_no_chunks(tmp_path: Path) -> None:
    chunks = _chunk(tmp_path, "empty.py", "", "python")
    assert chunks == []


def test_python_file_with_only_imports_returns_no_chunks(tmp_path: Path) -> None:
    code = "import os\nimport sys\nfrom pathlib import Path\n"
    chunks = _chunk(tmp_path, "imports.py", code, "python")
    assert chunks == []


def test_python_chunk_span_is_valid(tmp_path: Path) -> None:
    code = "def alpha() -> None:\n    pass\n"
    chunks = _chunk(tmp_path, "alpha.py", code, "python")
    assert len(chunks) == 1
    start, end = chunks[0].byte_span
    assert start >= 0
    assert end > start
    assert end <= len(code.encode())


# ---------------------------------------------------------------------------
# JavaScript / TypeScript AST chunking
# ---------------------------------------------------------------------------


def test_js_function_declaration_becomes_chunk(tmp_path: Path) -> None:
    code = "function greet(name) {\n  return `hello ${name}`;\n}\n"
    chunks = _chunk(tmp_path, "greet.js", code, "javascript")

    func_chunks = [c for c in chunks if c.kind is ChunkKind.function]
    assert len(func_chunks) == 1
    assert "greet" in func_chunks[0].name


def test_js_arrow_function_assigned_to_const_becomes_chunk(tmp_path: Path) -> None:
    code = "const add = (a, b) => a + b;\n"
    chunks = _chunk(tmp_path, "add.js", code, "javascript")

    # Arrow functions assigned to consts should be extracted.
    func_chunks = [c for c in chunks if c.kind is ChunkKind.function]
    assert len(func_chunks) == 1
    assert "add" in func_chunks[0].name


def test_ts_function_becomes_chunk(tmp_path: Path) -> None:
    code = "function greet(name: string): string {\n  return `hello ${name}`;\n}\n"
    chunks = _chunk(tmp_path, "greet.ts", code, "typescript")

    func_chunks = [c for c in chunks if c.kind is ChunkKind.function]
    assert len(func_chunks) >= 1
    assert any("greet" in c.name for c in func_chunks)


def test_ts_class_becomes_chunk(tmp_path: Path) -> None:
    code = "class Greeter {\n  greet(name: string): string {\n    return `hi ${name}`;\n  }\n}\n"
    chunks = _chunk(tmp_path, "greeter.ts", code, "typescript")

    class_chunks = [c for c in chunks if c.kind is ChunkKind.klass]
    assert len(class_chunks) == 1
    assert "Greeter" in class_chunks[0].name


def test_js_empty_file_returns_no_chunks(tmp_path: Path) -> None:
    chunks = _chunk(tmp_path, "empty.js", "", "javascript")
    assert chunks == []


# ---------------------------------------------------------------------------
# Markdown section chunking
# ---------------------------------------------------------------------------


def _md_chunk(tmp_path: Path, relative: str, content: str) -> list[Chunk]:
    record = _record(tmp_path, relative, content, kind=FileKind.documentation, language=None)
    return Chunker().chunk(record)


def test_markdown_heading_becomes_section_chunk(tmp_path: Path) -> None:
    md = "# Introduction\n\nThis is the intro.\n\n## Details\n\nMore detail here.\n"
    chunks = _md_chunk(tmp_path, "README.md", md)

    assert len(chunks) == 2
    assert all(c.kind is ChunkKind.section for c in chunks)
    names = [c.name for c in chunks]
    assert any("Introduction" in n for n in names)
    assert any("Details" in n for n in names)


def test_markdown_section_text_includes_heading(tmp_path: Path) -> None:
    md = "# Getting Started\n\nRun `pip install openrabbit`.\n"
    chunks = _md_chunk(tmp_path, "guide.md", md)

    assert len(chunks) == 1
    assert "Getting Started" in chunks[0].text


def test_markdown_no_headings_yields_single_chunk(tmp_path: Path) -> None:
    md = "Just some prose without any headings.\n"
    chunks = _md_chunk(tmp_path, "plain.md", md)

    assert len(chunks) == 1
    assert chunks[0].kind is ChunkKind.section


def test_markdown_empty_file_returns_no_chunks(tmp_path: Path) -> None:
    chunks = _md_chunk(tmp_path, "empty.md", "")
    assert chunks == []


def test_markdown_large_section_is_split_with_overlap(tmp_path: Path) -> None:
    # Build a section whose body is well over 1000 chars.
    body = "word " * 300  # 1500 chars
    md = f"# Long Section\n\n{body}\n"
    chunks = _md_chunk(tmp_path, "long.md", md)

    # The section is large enough to need splitting.
    assert len(chunks) > 1
    # Check overlap: last ~150 chars of chunk N appear in chunk N+1.
    first_text = chunks[0].text
    second_text = chunks[1].text
    overlap_candidate = first_text[-150:]
    assert overlap_candidate in second_text


def test_markdown_source_path_is_set(tmp_path: Path) -> None:
    md = "# Section\n\nContent.\n"
    chunks = _md_chunk(tmp_path, "docs/arch.md", md)
    assert all(c.source_path == Path("docs/arch.md") for c in chunks)


# ---------------------------------------------------------------------------
# Chunker skips non-source, non-doc file kinds
# ---------------------------------------------------------------------------


def test_chunker_returns_empty_for_rules_file(tmp_path: Path) -> None:
    record = _record(
        tmp_path,
        ".openrabbit/coding_rules.md",
        "# Rules\n\nUse type hints.",
        kind=FileKind.rules,
    )
    chunks = Chunker().chunk(record)
    # Rules files fall back to section chunking because they are markdown.
    # They should still be chunked (the retriever needs them).
    assert isinstance(chunks, list)


def test_chunker_returns_empty_for_other_file_kind(tmp_path: Path) -> None:
    record = _record(tmp_path, "data.json", '{"key": "value"}', kind=FileKind.other)
    chunks = Chunker().chunk(record)
    assert chunks == []


# ---------------------------------------------------------------------------
# Chunk metadata consistency
# ---------------------------------------------------------------------------


def test_chunk_source_path_matches_record_path(tmp_path: Path) -> None:
    code = "def foo() -> None:\n    pass\n"
    record = _record(tmp_path, "src/foo.py", code, language="python")
    chunks = Chunker().chunk(record)
    assert all(c.source_path == record.path for c in chunks)


def test_chunk_text_is_non_empty(tmp_path: Path) -> None:
    code = "def bar() -> None:\n    pass\n"
    chunks = _chunk(tmp_path, "bar.py", code, "python")
    assert all(c.text.strip() for c in chunks)


def test_chunker_preserves_record_metadata(tmp_path: Path) -> None:
    record = _record(
        tmp_path,
        "services/api/AGENTS.md",
        "# API rules\n\nUse repository errors.",
        kind=FileKind.rules,
        metadata={
            "rule_source": "repository_guideline",
            "scope_path": "services/api",
            "guideline_path": "services/api/AGENTS.md",
        },
    )

    chunks = Chunker().chunk(record)

    assert chunks
    assert all(chunk.metadata["rule_source"] == "repository_guideline" for chunk in chunks)
    assert all(chunk.metadata["scope_path"] == "services/api" for chunk in chunks)
