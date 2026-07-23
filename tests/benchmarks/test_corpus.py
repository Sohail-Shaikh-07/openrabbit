"""Tests for loading checked-in benchmark corpora."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks import CorpusFormatError, load_benchmark_cases
from benchmarks.corpus import DEFAULT_V1_1_CORPUS, DEFAULT_V1_7_CONTEXT_PRECISION_CORPUS


def test_load_default_v1_1_corpus() -> None:
    cases = load_benchmark_cases()

    assert len(cases) >= 6
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.case_id.startswith("v1_1-") for case in cases)
    assert all(case.title for case in cases)
    assert all(case.diff.startswith("diff --git") for case in cases)
    assert all(case.known_issues for case in cases)


def test_default_corpus_constant_points_to_packaged_file() -> None:
    assert DEFAULT_V1_1_CORPUS.is_file()


def test_load_v1_7_context_precision_corpus() -> None:
    cases = load_benchmark_cases(DEFAULT_V1_7_CONTEXT_PRECISION_CORPUS)

    assert len(cases) == 3
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.case_id.startswith("v1_7-context-") for case in cases)
    assert all(case.title for case in cases)
    assert all(case.diff.startswith("diff --git") for case in cases)
    assert {issue for case in cases for issue in case.known_issues} >= {
        "changed symbol context",
        "connector relevance",
        "low risk summary",
    }


def test_load_benchmark_cases_ignores_blank_lines(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "\n"
        '{"case_id":"case-1","title":"One","diff":"diff --git a/a b/a",'
        '"known_issues":["bug"]}\n'
        "\n",
        encoding="utf-8",
    )

    cases = load_benchmark_cases(corpus)

    assert len(cases) == 1
    assert cases[0].case_id == "case-1"


def test_missing_required_field_raises_format_error(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        '{"case_id":"case-1","diff":"diff --git a/a b/a"}\n',
        encoding="utf-8",
    )

    with pytest.raises(CorpusFormatError, match="known_issues"):
        load_benchmark_cases(corpus)


def test_duplicate_case_id_raises_format_error(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        '{"case_id":"dup","diff":"diff --git a/a b/a","known_issues":["bug"]}\n'
        '{"case_id":"dup","diff":"diff --git a/b b/b","known_issues":["bug"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(CorpusFormatError, match="duplicate case_id"):
        load_benchmark_cases(corpus)


def test_invalid_known_issues_type_raises_format_error(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        '{"case_id":"case-1","diff":"diff --git a/a b/a","known_issues":"bug"}\n',
        encoding="utf-8",
    )

    with pytest.raises(CorpusFormatError, match="known_issues must be a list"):
        load_benchmark_cases(corpus)


def test_empty_corpus_raises_format_error(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("\n\n", encoding="utf-8")

    with pytest.raises(CorpusFormatError, match="does not contain"):
        load_benchmark_cases(corpus)
