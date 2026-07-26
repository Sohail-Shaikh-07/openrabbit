"""Documentation update suggestion command."""

from __future__ import annotations

import asyncio
import fnmatch
import re
from dataclasses import dataclass
from typing import Any, TextIO

from cli.commands.output import render_json
from cli.commands.start import resolve_target_repo
from configs.settings import Settings
from github_ import GitHubClient, ParsedFile, PullRequestParser, RepositoryHandle

_MAX_SYMBOLS_PER_FILE = 8
_SYMBOL_RE = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class DocsRule:
    """One deterministic changed-path to documentation-target mapping."""

    category: str
    patterns: tuple[str, ...]
    targets: tuple[str, ...]
    reason: str
    confidence: float


@dataclass(frozen=True)
class DocsSuggestion:
    """One documentation update suggestion."""

    target_path: str
    category: str
    reason: str
    confidence: float
    source_paths: tuple[str, ...]
    public_symbols: tuple[str, ...]


_DOC_RULES: tuple[DocsRule, ...] = (
    DocsRule(
        category="cli",
        patterns=("src/cli/main.py", "src/cli/commands/*.py"),
        targets=("README.md", "docs/interactive-pr-workflows.md"),
        reason="CLI command behavior changed, so command usage docs may need an update.",
        confidence=0.94,
    ),
    DocsRule(
        category="github_actions",
        patterns=("examples/github-actions/*", ".github/workflows/*"),
        targets=("docs/github-actions.md", "README.md"),
        reason="GitHub Actions or examples changed, so workflow docs may need an update.",
        confidence=0.92,
    ),
    DocsRule(
        category="configuration",
        patterns=("src/configs/*.py", "src/cli/templates.py"),
        targets=("README.md", "docs/model-providers.md", "docs/knowledge-connectors.md"),
        reason="Configuration shape or generated templates changed.",
        confidence=0.9,
    ),
    DocsRule(
        category="connectors",
        patterns=("src/knowledge/*.py",),
        targets=("docs/knowledge-connectors.md", "README.md"),
        reason="Knowledge connector behavior changed.",
        confidence=0.88,
    ),
    DocsRule(
        category="memory",
        patterns=("src/memory/*.py",),
        targets=("docs/pr-memory.md", "docs/memory-backends.md"),
        reason="PR memory behavior changed.",
        confidence=0.86,
    ),
    DocsRule(
        category="eval",
        patterns=("src/cli/commands/eval.py", "src/benchmarks/*.py", "src/quality/*.py"),
        targets=("docs/eval-reporting.md", "docs/local-quality-gates.md"),
        reason="Evaluation or local quality-gate behavior changed.",
        confidence=0.86,
    ),
    DocsRule(
        category="rag",
        patterns=("src/rag/*.py",),
        targets=("docs/context-precision.md", "docs/repository-guidelines.md"),
        reason="Repository context or retrieval behavior changed.",
        confidence=0.84,
    ),
    DocsRule(
        category="release",
        patterns=("CHANGELOG.md", "docs/release-*.md"),
        targets=("README.md", "docs/pr-agent-gap-analysis.md"),
        reason="Release notes changed, so overview and parity docs may need alignment.",
        confidence=0.78,
    ),
    DocsRule(
        category="public_api",
        patterns=("src/github_/*.py", "src/agents/*.py"),
        targets=("README.md",),
        reason="Public integration or agent surfaces changed.",
        confidence=0.72,
    ),
)


async def run_docs_suggestions(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    limit: int = 10,
) -> dict[str, object]:
    """Fetch a PR and return deterministic documentation update suggestions."""
    if limit < 1:
        raise ValueError("--limit must be at least 1")

    target_repo = resolve_target_repo(settings, repo)
    client = GitHubClient.from_settings(settings, env=env)
    try:
        handle = RepositoryHandle.from_full_name(target_repo, client)
        parser = PullRequestParser(handle)
        payload = await parser.parse(number)
    finally:
        await client.aclose()

    suggestions = _suggest_docs(payload.files, limit=limit)
    return {
        "schema_version": "1.0",
        "command": "docs",
        "repo": handle.full_name,
        "number": payload.number,
        "title": payload.pull_request.title,
        "state": payload.pull_request.state,
        "head_sha": payload.head_sha[:12],
        "files_changed": len(payload.files),
        "changed_public_surface_count": _public_surface_count(payload.files),
        "suggestion_count": len(suggestions),
        "docs_suggestions": [_serialize_suggestion(suggestion) for suggestion in suggestions],
        "mutates_files": False,
        "mutates_github": False,
    }


def run_docs_suggestions_blocking(
    settings: Settings,
    *,
    number: int,
    repo: str | None = None,
    env: dict[str, str] | None = None,
    limit: int = 10,
) -> dict[str, object]:
    """Synchronous wrapper used by the Typer command."""
    return asyncio.run(
        run_docs_suggestions(
            settings,
            number=number,
            repo=repo,
            env=env,
            limit=limit,
        )
    )


def render_docs_suggestions(summary: dict[str, object], out: TextIO) -> None:
    """Pretty-print the dict returned by :func:`run_docs_suggestions`."""
    print(f"Documentation suggestions for PR #{summary['number']} on {summary['repo']}", file=out)
    print(f"Title:        {summary['title']}", file=out)
    print(f"Files:        {summary['files_changed']}", file=out)
    print(f"Suggestions:  {summary['suggestion_count']}", file=out)
    print("GitHub write: no", file=out)
    print("File write:   no", file=out)

    raw_suggestions = summary.get("docs_suggestions")
    suggestions = raw_suggestions if isinstance(raw_suggestions, list) else []
    if not suggestions:
        print("", file=out)
        print("No documentation follow-ups matched the changed paths.", file=out)
        return

    print("", file=out)
    print("Suggested documentation updates:", file=out)
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        print(
            f"- {suggestion.get('target_path', 'unknown')} "
            f"({suggestion.get('category', 'docs')}, {suggestion.get('confidence', 0):.2f})",
            file=out,
        )
        print(f"  Reason: {suggestion.get('reason', '')}", file=out)
        sources = suggestion.get("source_paths")
        if isinstance(sources, list) and sources:
            print(f"  Sources: {', '.join(str(source) for source in sources[:5])}", file=out)
        symbols = suggestion.get("public_symbols")
        if isinstance(symbols, list) and symbols:
            print(
                f"  Public surfaces: {', '.join(str(symbol) for symbol in symbols[:5])}", file=out
            )


def render_docs_suggestions_json(summary: dict[str, object], out: TextIO) -> None:
    """Render documentation suggestions as deterministic JSON."""
    render_json(summary, out)


def _suggest_docs(files: list[ParsedFile], *, limit: int) -> list[DocsSuggestion]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for changed_file in files:
        path = changed_file.path
        for rule in _matching_rules(path):
            symbols = _public_symbols(changed_file)
            for target in rule.targets:
                key = (target, rule.category)
                item = grouped.setdefault(
                    key,
                    {
                        "target_path": target,
                        "category": rule.category,
                        "reason": rule.reason,
                        "confidence": rule.confidence,
                        "source_paths": set(),
                        "public_symbols": set(),
                    },
                )
                item["source_paths"].add(path)
                item["public_symbols"].update(symbols)

    suggestions = [
        DocsSuggestion(
            target_path=str(item["target_path"]),
            category=str(item["category"]),
            reason=str(item["reason"]),
            confidence=float(item["confidence"]),
            source_paths=tuple(sorted(item["source_paths"])),
            public_symbols=tuple(sorted(item["public_symbols"])),
        )
        for item in grouped.values()
    ]
    return sorted(
        suggestions,
        key=lambda item: (-item.confidence, item.target_path, item.category),
    )[:limit]


def _matching_rules(path: str) -> list[DocsRule]:
    normalized = path.replace("\\", "/")
    return [
        rule
        for rule in _DOC_RULES
        if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in rule.patterns)
    ]


def _public_symbols(changed_file: ParsedFile) -> tuple[str, ...]:
    if not changed_file.file.patch:
        return ()
    symbols: list[str] = []
    for raw_line in changed_file.file.patch.splitlines():
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        line = raw_line[1:].strip()
        if line.startswith("@app.command"):
            symbols.append(line)
            continue
        match = _SYMBOL_RE.match(line)
        if match and not match.group(1).startswith("_"):
            symbols.append(match.group(1))
        if len(symbols) >= _MAX_SYMBOLS_PER_FILE:
            break
    return tuple(dict.fromkeys(symbols))


def _public_surface_count(files: list[ParsedFile]) -> int:
    return sum(1 for changed_file in files if _matching_rules(changed_file.path))


def _serialize_suggestion(suggestion: DocsSuggestion) -> dict[str, object]:
    return {
        "target_path": suggestion.target_path,
        "category": suggestion.category,
        "reason": suggestion.reason,
        "confidence": suggestion.confidence,
        "source_paths": list(suggestion.source_paths),
        "public_symbols": list(suggestion.public_symbols),
    }
