"""Local daemon lifecycle helpers for OpenRabbit."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

STATE_SUBDIR = ".openrabbit"
DAEMON_STATE_FILENAME = "daemon.json"

StopStatus = Literal["not_running", "stale", "stopped", "failed"]


@dataclass(frozen=True)
class DaemonState:
    """The minimal local metadata needed to stop a foreground daemon."""

    pid: int
    repo: str
    started_at: str


@dataclass(frozen=True)
class StopResult:
    """Result returned by :func:`run_stop`."""

    status: StopStatus
    message: str
    pid: int | None = None


def daemon_state_path(workspace: Path) -> Path:
    """Return the local daemon metadata path for a workspace."""
    return workspace / STATE_SUBDIR / DAEMON_STATE_FILENAME


def write_daemon_state(workspace: Path, *, pid: int, repo: str) -> DaemonState:
    """Persist local daemon metadata atomically."""
    state = DaemonState(
        pid=pid,
        repo=repo,
        started_at=datetime.now(UTC).isoformat(),
    )
    path = daemon_state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "pid": state.pid,
        "repo": state.repo,
        "started_at": state.started_at,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return state


def clear_daemon_state(workspace: Path) -> None:
    """Remove local daemon metadata if it exists."""
    path = daemon_state_path(workspace)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def read_daemon_state(workspace: Path) -> DaemonState | None:
    """Read daemon metadata, returning ``None`` for missing or invalid state."""
    path = daemon_state_path(workspace)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DaemonState(
            pid=int(raw["pid"]),
            repo=str(raw.get("repo") or ""),
            started_at=str(raw.get("started_at") or ""),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        clear_daemon_state(workspace)
        return None


def daemon_state_is_running(state: DaemonState) -> bool:
    """Return whether the daemon metadata points to a currently running PID."""
    return _pid_exists(state.pid)


def run_stop(workspace: Path, *, timeout_seconds: float = 10.0) -> StopResult:
    """Stop the daemon recorded for ``workspace``.

    Missing and stale state are successful no-op outcomes. The command only
    targets the PID recorded in the local workspace metadata.
    """
    state = read_daemon_state(workspace)
    if state is None:
        return StopResult(status="not_running", message="OpenRabbit daemon is not running.")

    if not daemon_state_is_running(state):
        clear_daemon_state(workspace)
        return StopResult(
            status="stale",
            pid=state.pid,
            message=f"Removed stale OpenRabbit daemon state for PID {state.pid}.",
        )

    stop_error = _terminate_pid(state.pid)
    if stop_error == "missing":
        clear_daemon_state(workspace)
        return StopResult(
            status="stale",
            pid=state.pid,
            message=f"Removed stale OpenRabbit daemon state for PID {state.pid}.",
        )
    if stop_error == "permission":
        return StopResult(
            status="failed",
            pid=state.pid,
            message=f"OpenRabbit daemon PID {state.pid} exists but could not be stopped.",
        )

    _wait_for_exit(state.pid, timeout_seconds=timeout_seconds)
    clear_daemon_state(workspace)
    return StopResult(
        status="stopped",
        pid=state.pid,
        message=f"Stopped OpenRabbit daemon PID {state.pid}.",
    )


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> Literal["ok", "missing", "permission"]:
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return "ok"
        output = f"{result.stdout}\n{result.stderr}".lower()
        if "not found" in output or "not running" in output:
            return "missing"
        if "access is denied" in output:
            return "permission"
        return "missing" if not _pid_exists(pid) else "permission"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "missing"
    except PermissionError:
        return "permission"
    return "ok"


def _wait_for_exit(pid: int, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return
        time.sleep(0.05)
