"""OpenRabbit PR comment command parsing and local command state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

CommandKind = Literal[
    "review",
    "full_review",
    "improve",
    "ask",
    "pause",
    "resume",
    "ignore",
    "summary",
    "configuration",
    "learn",
]

_COMMAND_RE = re.compile(r"^\s*(?:/|@)openrabbit(?:\s+(.+?))?\s*$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class PullRequestCommand:
    """One command addressed to OpenRabbit in a PR comment."""

    kind: CommandKind
    question: str = ""
    instruction: str = ""


@dataclass(frozen=True)
class CommandState:
    """Local state for PR command processing."""

    paused_prs: frozenset[int] = field(default_factory=frozenset)
    ignored_prs: frozenset[int] = field(default_factory=frozenset)
    last_seen_comment_ids: dict[int, int] = field(default_factory=dict)
    completed_comment_ids: dict[int, frozenset[int]] = field(default_factory=dict)
    comment_failure_attempts: dict[int, dict[int, int]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> CommandState:
        return cls()

    def is_paused(self, pr_number: int) -> bool:
        return pr_number in self.paused_prs

    def is_ignored(self, pr_number: int) -> bool:
        return pr_number in self.ignored_prs

    def last_seen_comment_id(self, pr_number: int) -> int:
        return self.last_seen_comment_ids.get(pr_number, 0)

    def is_comment_completed(self, pr_number: int, comment_id: int) -> bool:
        return comment_id <= self.last_seen_comment_id(
            pr_number
        ) or comment_id in self.completed_comment_ids.get(pr_number, frozenset())

    def failure_attempts(self, pr_number: int, comment_id: int) -> int:
        return self.comment_failure_attempts.get(pr_number, {}).get(comment_id, 0)

    def pause(self, pr_number: int) -> CommandState:
        paused = set(self.paused_prs)
        paused.add(pr_number)
        return CommandState(
            paused_prs=frozenset(paused),
            ignored_prs=self.ignored_prs,
            last_seen_comment_ids=dict(self.last_seen_comment_ids),
            completed_comment_ids=dict(self.completed_comment_ids),
            comment_failure_attempts=_copy_failure_attempts(self.comment_failure_attempts),
        )

    def resume(self, pr_number: int) -> CommandState:
        paused = set(self.paused_prs)
        ignored = set(self.ignored_prs)
        paused.discard(pr_number)
        ignored.discard(pr_number)
        return CommandState(
            paused_prs=frozenset(paused),
            ignored_prs=frozenset(ignored),
            last_seen_comment_ids=dict(self.last_seen_comment_ids),
            completed_comment_ids=dict(self.completed_comment_ids),
            comment_failure_attempts=_copy_failure_attempts(self.comment_failure_attempts),
        )

    def ignore(self, pr_number: int) -> CommandState:
        ignored = set(self.ignored_prs)
        ignored.add(pr_number)
        return CommandState(
            paused_prs=self.paused_prs,
            ignored_prs=frozenset(ignored),
            last_seen_comment_ids=dict(self.last_seen_comment_ids),
            completed_comment_ids=dict(self.completed_comment_ids),
            comment_failure_attempts=_copy_failure_attempts(self.comment_failure_attempts),
        )

    def mark_comment_seen(self, pr_number: int, comment_id: int) -> CommandState:
        cursors = dict(self.last_seen_comment_ids)
        cursors[pr_number] = max(comment_id, cursors.get(pr_number, 0))
        completed = _copy_completed_comments(self.completed_comment_ids)
        completed[pr_number] = frozenset(
            value for value in completed.get(pr_number, frozenset()) if value > cursors[pr_number]
        )
        failures = _copy_failure_attempts(self.comment_failure_attempts)
        failures[pr_number] = {
            value: attempts
            for value, attempts in failures.get(pr_number, {}).items()
            if value > cursors[pr_number]
        }
        return CommandState(
            paused_prs=self.paused_prs,
            ignored_prs=self.ignored_prs,
            last_seen_comment_ids=cursors,
            completed_comment_ids=_without_empty_sets(completed),
            comment_failure_attempts=_without_empty_mappings(failures),
        )

    def mark_comment_completed(self, pr_number: int, comment_id: int) -> CommandState:
        if comment_id <= self.last_seen_comment_id(pr_number):
            return self
        completed = _copy_completed_comments(self.completed_comment_ids)
        completed[pr_number] = completed.get(pr_number, frozenset()) | {comment_id}
        failures = _copy_failure_attempts(self.comment_failure_attempts)
        failures.get(pr_number, {}).pop(comment_id, None)
        return CommandState(
            paused_prs=self.paused_prs,
            ignored_prs=self.ignored_prs,
            last_seen_comment_ids=dict(self.last_seen_comment_ids),
            completed_comment_ids=completed,
            comment_failure_attempts=_without_empty_mappings(failures),
        )

    def record_comment_failure(self, pr_number: int, comment_id: int) -> CommandState:
        failures = _copy_failure_attempts(self.comment_failure_attempts)
        pr_failures = failures.setdefault(pr_number, {})
        pr_failures[comment_id] = pr_failures.get(comment_id, 0) + 1
        return CommandState(
            paused_prs=self.paused_prs,
            ignored_prs=self.ignored_prs,
            last_seen_comment_ids=dict(self.last_seen_comment_ids),
            completed_comment_ids=dict(self.completed_comment_ids),
            comment_failure_attempts=failures,
        )

    def advance_comment_cursor(
        self,
        pr_number: int,
        ordered_comment_ids: list[int],
    ) -> CommandState:
        cursor = self.last_seen_comment_id(pr_number)
        completed = self.completed_comment_ids.get(pr_number, frozenset())
        for comment_id in ordered_comment_ids:
            if comment_id <= cursor:
                continue
            if comment_id not in completed:
                break
            cursor = comment_id
        return self.mark_comment_seen(pr_number, cursor)


def _copy_completed_comments(
    values: dict[int, frozenset[int]],
) -> dict[int, frozenset[int]]:
    return {pr_number: frozenset(comment_ids) for pr_number, comment_ids in values.items()}


def _copy_failure_attempts(values: dict[int, dict[int, int]]) -> dict[int, dict[int, int]]:
    return {pr_number: dict(attempts) for pr_number, attempts in values.items()}


def _without_empty_sets(values: dict[int, frozenset[int]]) -> dict[int, frozenset[int]]:
    return {pr_number: comment_ids for pr_number, comment_ids in values.items() if comment_ids}


def _without_empty_mappings(values: dict[int, dict[int, int]]) -> dict[int, dict[int, int]]:
    return {pr_number: attempts for pr_number, attempts in values.items() if attempts}


class CommandStateStore(Protocol):
    """Anything that can round-trip local PR command state."""

    def load(self) -> CommandState: ...

    def save(self, state: CommandState) -> None: ...


class InMemoryCommandStateStore:
    """In-memory command state store for tests."""

    def __init__(self, initial: CommandState | None = None) -> None:
        self._state = initial or CommandState.empty()

    def load(self) -> CommandState:
        return self._state

    def save(self, state: CommandState) -> None:
        self._state = state


class FileCommandStateStore:
    """JSON-on-disk command state store."""

    SCHEMA_VERSION = 2
    LEGACY_SCHEMA_VERSIONS = frozenset({1})

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> CommandState:
        if not self._path.is_file():
            return CommandState.empty()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if raw.get("version") not in self.LEGACY_SCHEMA_VERSIONS | {self.SCHEMA_VERSION}:
            return CommandState.empty()
        paused = frozenset(int(value) for value in raw.get("paused_prs", []))
        ignored = frozenset(int(value) for value in raw.get("ignored_prs", []))
        cursors = {
            int(pr_number): int(comment_id)
            for pr_number, comment_id in raw.get("last_seen_comment_ids", {}).items()
        }
        completed = {
            int(pr_number): frozenset(int(comment_id) for comment_id in comment_ids)
            for pr_number, comment_ids in raw.get("completed_comment_ids", {}).items()
        }
        failures = {
            int(pr_number): {
                int(comment_id): int(attempts) for comment_id, attempts in pr_failures.items()
            }
            for pr_number, pr_failures in raw.get("comment_failure_attempts", {}).items()
        }
        return CommandState(
            paused_prs=paused,
            ignored_prs=ignored,
            last_seen_comment_ids=cursors,
            completed_comment_ids=completed,
            comment_failure_attempts=failures,
        )

    def save(self, state: CommandState) -> None:
        payload = {
            "version": self.SCHEMA_VERSION,
            "paused_prs": sorted(state.paused_prs),
            "ignored_prs": sorted(state.ignored_prs),
            "last_seen_comment_ids": {
                str(pr): comment_id
                for pr, comment_id in sorted(state.last_seen_comment_ids.items())
            },
            "completed_comment_ids": {
                str(pr): sorted(comment_ids)
                for pr, comment_ids in sorted(state.completed_comment_ids.items())
            },
            "comment_failure_attempts": {
                str(pr): {
                    str(comment_id): attempts
                    for comment_id, attempts in sorted(pr_failures.items())
                }
                for pr, pr_failures in sorted(state.comment_failure_attempts.items())
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)


def parse_openrabbit_command(body: str) -> PullRequestCommand | None:
    """Parse a PR comment body into an OpenRabbit command, if present."""
    first_command_line = _first_command_line(body)
    if first_command_line is None:
        return None

    match = _COMMAND_RE.match(first_command_line)
    if match is None:
        return None
    raw = " ".join((match.group(1) or "").split())
    lowered = raw.lower()
    if lowered == "review":
        return PullRequestCommand(kind="review")
    if lowered == "full review":
        return PullRequestCommand(kind="full_review")
    if lowered == "improve":
        return PullRequestCommand(kind="improve")
    if lowered == "pause":
        return PullRequestCommand(kind="pause")
    if lowered == "resume":
        return PullRequestCommand(kind="resume")
    if lowered == "ignore":
        return PullRequestCommand(kind="ignore")
    if lowered == "summary":
        return PullRequestCommand(kind="summary")
    if lowered in {"configuration", "config"}:
        return PullRequestCommand(kind="configuration")
    if lowered.startswith("ask "):
        question = raw[4:].strip()
        if question:
            return PullRequestCommand(kind="ask", question=question)
    if lowered.startswith("learn "):
        instruction = raw[6:].strip()
        if instruction:
            return PullRequestCommand(kind="learn", instruction=instruction)
    return None


def _first_command_line(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(("/openrabbit", "@openrabbit")):
            return line
    return None
