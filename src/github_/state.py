"""Persistent state for the polling service.

The polling loop needs to remember which pull requests it has seen, when they
were last updated, and the head sha of each so it can tell ``updated`` apart
from ``commit_pushed`` on the next round. We persist that under
``.openrabbit/state.json`` next to the target repo so restarts do not
re-process every open PR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SeenPullRequest:
    """The minimal slice of PR state we need to detect changes."""

    number: int
    updated_at: str
    head_sha: str


@dataclass(frozen=True)
class PollState:
    """Snapshot of what the polling loop saw the last time it ran."""

    pull_requests: dict[int, SeenPullRequest] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> PollState:
        return cls(pull_requests={})

    def with_pull_request(self, pr: SeenPullRequest) -> PollState:
        merged = dict(self.pull_requests)
        merged[pr.number] = pr
        return PollState(pull_requests=merged)


class StateStore(Protocol):
    """Anything that can round-trip a :class:`PollState`."""

    def load(self) -> PollState: ...

    def save(self, state: PollState) -> None: ...


class InMemoryStateStore:
    """Default ``StateStore`` used by tests so they do not touch the filesystem."""

    def __init__(self, initial: PollState | None = None) -> None:
        self._state = initial or PollState.empty()

    def load(self) -> PollState:
        return self._state

    def save(self, state: PollState) -> None:
        self._state = state


class FileStateStore:
    """JSON-on-disk implementation. Atomic via write-then-rename."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> PollState:
        if not self._path.is_file():
            return PollState.empty()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if raw.get("version") != self.SCHEMA_VERSION:
            # Treat unknown versions as a clean slate. The polling loop will
            # re-seed without firing events.
            return PollState.empty()
        prs = {
            int(entry["number"]): SeenPullRequest(
                number=int(entry["number"]),
                updated_at=str(entry["updated_at"]),
                head_sha=str(entry["head_sha"]),
            )
            for entry in raw.get("pull_requests", [])
        }
        return PollState(pull_requests=prs)

    def save(self, state: PollState) -> None:
        payload = {
            "version": self.SCHEMA_VERSION,
            "pull_requests": [
                {
                    "number": pr.number,
                    "updated_at": pr.updated_at,
                    "head_sha": pr.head_sha,
                }
                for pr in state.pull_requests.values()
            ],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)
