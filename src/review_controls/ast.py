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
        if not _matches_repository_path(path, rule.path):
            continue
        if rule.languages and language not in rule.languages:
            continue
        for symbol in symbols:
            if symbol.kind.value not in rule.symbols:
                continue
            if not fnmatch.fnmatchcase(symbol.name, rule.name_pattern):
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


def _is_arrow_function_declarator(node: Node) -> bool:
    value = node.child_by_field_name("value")
    while value is not None and value.type in {
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
        "type_assertion",
    }:
        named_children = value.named_children
        if not named_children:
            return False
        value = named_children[-1] if value.type == "type_assertion" else named_children[0]
    return value is not None and value.type == "arrow_function"


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
    stack: list[tuple[Node, bool]] = [(child, inside_class) for child in reversed(node.children)]
    while stack:
        current, current_inside_class = stack.pop()
        child_inside_class = current_inside_class and current.type != "function_definition"
        if current.type in _CLASS_NODES:
            _append_symbol(
                out,
                node=current,
                language=language,
                kind=AstSymbolKind.klass,
                source_bytes=source_bytes,
            )
            child_inside_class = True
        elif current.type == "function_definition":
            _append_symbol(
                out,
                node=current,
                language=language,
                kind=AstSymbolKind.method if current_inside_class else AstSymbolKind.function,
                source_bytes=source_bytes,
            )
        elif current.type == "function_declaration":
            _append_symbol(
                out,
                node=current,
                language=language,
                kind=AstSymbolKind.function,
                source_bytes=source_bytes,
            )
        elif current.type == "method_definition":
            _append_symbol(
                out,
                node=current,
                language=language,
                kind=AstSymbolKind.method,
                source_bytes=source_bytes,
            )
        elif current.type == "variable_declarator" and _is_arrow_function_declarator(current):
            _append_symbol(
                out,
                node=current,
                language=language,
                kind=AstSymbolKind.function,
                source_bytes=source_bytes,
            )
        stack.extend((child, child_inside_class) for child in reversed(current.children))


def _matches_repository_path(path: str, pattern: str) -> bool:
    path_parts = _repository_path_parts(path)
    pattern_parts = _repository_path_parts(pattern)
    stack = [(0, 0)]
    seen: set[tuple[int, int]] = set()

    while stack:
        pattern_index, path_index = stack.pop()
        state = (pattern_index, path_index)
        if state in seen:
            continue
        seen.add(state)
        if pattern_index == len(pattern_parts):
            if path_index == len(path_parts):
                return True
            continue

        part = pattern_parts[pattern_index]
        if part == "**":
            stack.append((pattern_index + 1, path_index))
            if path_index < len(path_parts):
                stack.append((pattern_index, path_index + 1))
        elif path_index < len(path_parts) and fnmatch.fnmatchcase(path_parts[path_index], part):
            stack.append((pattern_index + 1, path_index + 1))
    return False


def _repository_path_parts(value: str) -> list[str]:
    normalized = value.replace("\\", "/")
    return normalized.split("/") if normalized else []
