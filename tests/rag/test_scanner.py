"""Tests for ``rag.scanner``."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag import FileKind, FileRecord, IgnoreMatcher, RepositoryScanner


def _write(repo: Path, relative: str, content: str = "x") -> Path:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _scan(repo: Path, *, include_other: bool = False) -> list[FileRecord]:
    return list(RepositoryScanner(include_other=include_other).scan(repo))


def test_scan_raises_when_root_is_not_a_directory(tmp_path: Path) -> None:
    file_target = tmp_path / "f.txt"
    file_target.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        list(RepositoryScanner().scan(file_target))


def test_classifies_source_documentation_tests_and_rules(scaffold_repo: Path) -> None:
    _write(scaffold_repo, "src/app.py", "print(1)")
    _write(scaffold_repo, "tests/test_app.py", "def test_x(): pass")
    _write(scaffold_repo, "docs/architecture.md", "# arch")
    _write(scaffold_repo, "README.md", "hi")

    records = _scan(scaffold_repo)
    by_path = {r.path.as_posix(): r for r in records}

    assert by_path["src/app.py"].kind is FileKind.source
    assert by_path["src/app.py"].language == "python"
    assert by_path["tests/test_app.py"].kind is FileKind.tests
    assert by_path["docs/architecture.md"].kind is FileKind.documentation
    assert by_path["README.md"].kind is FileKind.documentation

    # Files under .codereviewer/ are rules.
    rule_paths = {r.path.as_posix() for r in records if r.kind is FileKind.rules}
    assert ".codereviewer/coding_rules.md" in rule_paths
    assert ".codereviewer/security_rules.md" in rule_paths


def test_other_files_are_skipped_by_default(scaffold_repo: Path) -> None:
    _write(scaffold_repo, "data/cargo.lock", "[lock]")
    _write(scaffold_repo, "src/app.py", "x")

    paths = {r.path.as_posix() for r in _scan(scaffold_repo)}
    assert "data/cargo.lock" not in paths
    assert "src/app.py" in paths


def test_include_other_returns_other_files(scaffold_repo: Path) -> None:
    _write(scaffold_repo, "data/cargo.lock", "[lock]")

    paths = {r.path.as_posix() for r in _scan(scaffold_repo, include_other=True)}
    assert "data/cargo.lock" in paths


def test_ignore_txt_is_honored(scaffold_repo: Path) -> None:
    _write(scaffold_repo, "src/app.py", "x")
    _write(scaffold_repo, "src/generated.py", "x")

    ignore = scaffold_repo / ".codereviewer" / "ignore.txt"
    # The init template already ignores generated dirs; add a single-file rule.
    ignore.write_text("src/generated.py\n", encoding="utf-8")

    paths = {r.path.as_posix() for r in _scan(scaffold_repo)}
    assert "src/app.py" in paths
    assert "src/generated.py" not in paths


def test_builtin_ignores_drop_caches_and_vcs(scaffold_repo: Path) -> None:
    _write(scaffold_repo, ".git/HEAD", "ref: refs/heads/main")
    _write(scaffold_repo, "src/__pycache__/app.cpython-312.pyc", "")
    _write(scaffold_repo, "src/app.py", "x")

    paths = {r.path.as_posix() for r in _scan(scaffold_repo)}
    assert ".git/HEAD" not in paths
    assert all("__pycache__" not in p for p in paths)
    assert "src/app.py" in paths


def test_scan_results_are_deterministic(scaffold_repo: Path) -> None:
    _write(scaffold_repo, "src/b.py", "x")
    _write(scaffold_repo, "src/a.py", "x")
    _write(scaffold_repo, "src/c.py", "x")

    first = [r.path.as_posix() for r in _scan(scaffold_repo) if r.kind is FileKind.source]
    second = [r.path.as_posix() for r in _scan(scaffold_repo) if r.kind is FileKind.source]
    assert first == sorted(first)
    assert first == second


def test_test_file_name_patterns_recognized(scaffold_repo: Path) -> None:
    _write(scaffold_repo, "src/foo_test.py", "x")
    _write(scaffold_repo, "src/feature.spec.ts", "x")
    _write(scaffold_repo, "src/feature.test.tsx", "x")

    by_path = {r.path.as_posix(): r for r in _scan(scaffold_repo)}
    assert by_path["src/foo_test.py"].kind is FileKind.tests
    assert by_path["src/feature.spec.ts"].kind is FileKind.tests
    assert by_path["src/feature.test.tsx"].kind is FileKind.tests


def test_ignore_matcher_skips_comments_and_blank_lines() -> None:
    matcher = IgnoreMatcher(["", "# comment", "build/**"])
    assert matcher.patterns == ["build/**"]


def test_ignore_matcher_basename_pattern_matches_any_depth() -> None:
    matcher = IgnoreMatcher(["*.pyc"])
    assert matcher.matches("foo.pyc")
    assert matcher.matches("deep/dir/foo.pyc")
    assert not matcher.matches("foo.py")
