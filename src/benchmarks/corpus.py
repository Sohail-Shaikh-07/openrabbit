"""Load checked-in benchmark corpora for OpenRabbit regression runs."""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from benchmarks.schema import BenchmarkCase

DEFAULT_V1_1_CORPUS = files("benchmarks").joinpath("corpora/v1_1_regression.jsonl")

CorpusSource = str | Path | Traversable


class CorpusFormatError(ValueError):
    """Raised when a benchmark corpus record is malformed."""


def load_benchmark_cases(source: CorpusSource | None = None) -> list[BenchmarkCase]:
    """Load benchmark cases from a JSONL corpus.

    Each non-empty line must be a JSON object with a unique ``case_id``, a
    non-empty unified ``diff`` string, and a non-empty ``known_issues`` list of
    strings. ``title`` is optional.
    """

    corpus_source = source or DEFAULT_V1_1_CORPUS
    lines = _read_lines(corpus_source)
    cases: list[BenchmarkCase] = []
    seen_case_ids: set[str] = set()

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        record = _parse_record(line, line_number)
        case = _record_to_case(record, line_number)
        if case.case_id in seen_case_ids:
            raise CorpusFormatError(f"line {line_number}: duplicate case_id {case.case_id!r}")
        seen_case_ids.add(case.case_id)
        cases.append(case)

    if not cases:
        raise CorpusFormatError("corpus does not contain any benchmark cases")

    return cases


def _read_lines(source: CorpusSource) -> list[str]:
    if isinstance(source, Traversable):
        if not source.is_file():
            raise FileNotFoundError(str(source))
        return source.read_text(encoding="utf-8").splitlines()

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8").splitlines()


def _parse_record(line: str, line_number: int) -> dict[str, Any]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise CorpusFormatError(f"line {line_number}: invalid JSON: {exc.msg}") from exc

    if not isinstance(record, dict):
        raise CorpusFormatError(f"line {line_number}: record must be a JSON object")
    return record


def _record_to_case(record: dict[str, Any], line_number: int) -> BenchmarkCase:
    case_id = _required_string(record, "case_id", line_number)
    diff = _required_string(record, "diff", line_number)
    known_issues = _required_string_list(record, "known_issues", line_number)
    title_value = record.get("title", "")

    if not isinstance(title_value, str):
        raise CorpusFormatError(f"line {line_number}: title must be a string")

    return BenchmarkCase(
        case_id=case_id,
        diff=diff,
        known_issues=known_issues,
        title=title_value,
    )


def _required_string(record: dict[str, Any], field: str, line_number: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CorpusFormatError(f"line {line_number}: {field} must be a non-empty string")
    return value


def _required_string_list(record: dict[str, Any], field: str, line_number: int) -> list[str]:
    value = record.get(field)
    if not isinstance(value, list):
        raise CorpusFormatError(f"line {line_number}: {field} must be a list of strings")

    strings: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise CorpusFormatError(
                f"line {line_number}: {field}[{index}] must be a non-empty string"
            )
        strings.append(item)

    if not strings:
        raise CorpusFormatError(f"line {line_number}: {field} must not be empty")

    return strings
