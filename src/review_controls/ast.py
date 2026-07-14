"""Tree-sitter symbol extraction for AST-scoped review instructions."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from configs.schema import AstInstruction


class AstSymbolKind(StrEnum):
    """Kinds of source symbols supported by AST review instructions."""

    function = "function"
    method = "method"
    klass = "class"


@dataclass(frozen=True)
class AstSymbol:
    """A named source symbol with a one-based inclusive line span."""

    language: str
    kind: AstSymbolKind
    name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class AstInstructionMatch:
    """One AST instruction that applies to a changed source symbol."""

    rule_index: int
    path: str
    symbol: AstSymbol
    instructions: str


_CLASS_NODES = {"class_definition", "class_declaration"}


def language_for_path(path: str) -> str | None:
    """Return the supported Tree-sitter language for a file path."""
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
    }.get(Path(path).suffix.lower())


def extract_ast_symbols(source: str, language: str) -> list[AstSymbol]:
    """Extract supported named symbols from source in source order."""
    if language not in {"python", "javascript", "typescript"}:
        return []
    parser = get_parser(language)
    source_bytes = source.encode("utf-8")
    root = parser.parse(source_bytes).root_node
    symbols: list[AstSymbol] = []
    _walk(root, source_bytes, language, symbols, inside_class=False)
    return symbols


def added_lines(file_: Any) -> frozenset[int]:
    """Return one-based new-file line numbers represented by additions."""
    changed: set[int] = set()
    for hunk in getattr(file_, "hunks", []):
        new_line = hunk.new_start
        for line in hunk.lines:
            if line.kind == "addition":
                changed.add(new_line)
                new_line += 1
            elif line.kind == "context":
                new_line += 1
    return frozenset(changed)


def match_ast_instructions(
    file_: Any,
    rules: list[AstInstruction],
) -> list[AstInstructionMatch]:
    """Match AST instructions whose constraints cover an added source line."""
    path = str(getattr(file_, "path", ""))
    source = getattr(file_, "source_text", None)
    language = language_for_path(path)
    additions = added_lines(file_)
    if not source or language is None or not additions:
        return []

    symbols = extract_ast_symbols(source, language)
    matches: list[AstInstructionMatch] = []
    seen: set[tuple[int, str, AstSymbolKind, str, int, int]] = set()
    for rule_index, rule in enumerate(rules):
        if not fnmatch.fnmatch(path, rule.path):
            continue
        if rule.languages and language not in rule.languages:
            continue
        for symbol in symbols:
            if symbol.kind.value not in rule.symbols:
                continue
            if not fnmatch.fnmatch(symbol.name, rule.name_pattern):
                continue
            if not any(symbol.start_line <= line <= symbol.end_line for line in additions):
                continue
            key = (
                rule_index,
                path,
                symbol.kind,
                symbol.name,
                symbol.start_line,
                symbol.end_line,
            )
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                AstInstructionMatch(
                    rule_index=rule_index,
                    path=path,
                    symbol=symbol,
                    instructions=rule.instructions,
                )
            )
    return matches


def _node_name(node: Node, source_bytes: bytes) -> str:
    name = node.child_by_field_name("name")
    if name is None:
        return "<anonymous>"
    return source_bytes[name.start_byte : name.end_byte].decode("utf-8", errors="replace")


def _contains_arrow_function(node: Node) -> bool:
    return any(
        child.type == "arrow_function" or _contains_arrow_function(child) for child in node.children
    )


def _append_symbol(
    out: list[AstSymbol],
    *,
    node: Node,
    language: str,
    kind: AstSymbolKind,
    source_bytes: bytes,
) -> None:
    out.append(
        AstSymbol(
            language=language,
            kind=kind,
            name=_node_name(node, source_bytes),
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
        )
    )


def _walk(
    node: Node,
    source_bytes: bytes,
    language: str,
    out: list[AstSymbol],
    *,
    inside_class: bool,
) -> None:
    for child in node.children:
        if child.type in _CLASS_NODES:
            _append_symbol(
                out,
                node=child,
                language=language,
                kind=AstSymbolKind.klass,
                source_bytes=source_bytes,
            )
            _walk(child, source_bytes, language, out, inside_class=True)
            continue
        if child.type == "function_definition":
            _append_symbol(
                out,
                node=child,
                language=language,
                kind=AstSymbolKind.method if inside_class else AstSymbolKind.function,
                source_bytes=source_bytes,
            )
        elif child.type == "function_declaration":
            _append_symbol(
                out,
                node=child,
                language=language,
                kind=AstSymbolKind.function,
                source_bytes=source_bytes,
            )
        elif child.type == "method_definition":
            _append_symbol(
                out,
                node=child,
                language=language,
                kind=AstSymbolKind.method,
                source_bytes=source_bytes,
            )
        elif child.type == "variable_declarator" and _contains_arrow_function(child):
            _append_symbol(
                out,
                node=child,
                language=language,
                kind=AstSymbolKind.function,
                source_bytes=source_bytes,
            )
        _walk(
            child,
            source_bytes,
            language,
            out,
            inside_class=inside_class and child.type != "function_definition",
        )
