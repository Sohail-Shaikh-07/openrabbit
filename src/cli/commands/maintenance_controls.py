"""Shared workflow control metadata for repository maintenance commands."""

from __future__ import annotations

from typing import Literal, TextIO

MaintenanceMode = Literal["read_only", "dry_run", "apply", "publish"]

_NO_WRITE = {
    "requested": False,
    "mutates": False,
    "operation": "none",
    "status": "not_requested",
}
_NO_FILE_WRITE = {
    "requested": False,
    "mutates": False,
    "operation": "none",
    "status": "not_supported",
}
_NO_MANAGED_COMMENT = {
    "requested": False,
    "enabled": False,
    "operation": "none",
    "status": "not_supported",
}


def build_workflow_controls(
    *,
    mode: MaintenanceMode,
    required_permissions: tuple[str, ...],
    github_write_requested: bool = False,
    github_write_operation: str = "none",
    github_write_status: str = "not_requested",
    mutates_github: bool = False,
    file_write_requested: bool = False,
    file_write_operation: str = "none",
    file_write_status: str = "not_supported",
    mutates_files: bool = False,
    managed_comment_requested: bool = False,
    managed_comment_enabled: bool = False,
    managed_comment_operation: str = "none",
    managed_comment_status: str = "not_supported",
) -> dict[str, object]:
    """Return stable workflow metadata for maintenance command outputs."""
    return {
        "mode": mode,
        "dry_run": mode in {"read_only", "dry_run"},
        "required_permissions": list(dict.fromkeys(required_permissions)),
        "github": (
            {
                "requested": github_write_requested,
                "mutates": mutates_github,
                "operation": github_write_operation,
                "status": github_write_status,
            }
            if github_write_requested or mutates_github or github_write_operation != "none"
            else dict(_NO_WRITE)
        ),
        "files": (
            {
                "requested": file_write_requested,
                "mutates": mutates_files,
                "operation": file_write_operation,
                "status": file_write_status,
            }
            if file_write_requested or mutates_files or file_write_operation != "none"
            else dict(_NO_FILE_WRITE)
        ),
        "managed_comment": (
            {
                "requested": managed_comment_requested,
                "enabled": managed_comment_enabled,
                "operation": managed_comment_operation,
                "status": managed_comment_status,
            }
            if managed_comment_requested
            or managed_comment_enabled
            or managed_comment_operation != "none"
            else dict(_NO_MANAGED_COMMENT)
        ),
    }


def read_only_workflow_controls(*, required_permissions: tuple[str, ...]) -> dict[str, object]:
    """Return controls for commands that do not support writes."""
    return build_workflow_controls(
        mode="read_only",
        required_permissions=required_permissions,
    )


def render_workflow_control_lines(summary: dict[str, object], out: TextIO) -> bool:
    """Print compact workflow control lines when a summary has the shared contract."""
    controls = summary.get("workflow_controls")
    if not isinstance(controls, dict):
        return False

    mode = str(controls.get("mode") or "unknown")
    print(f"Mode:         {mode}", file=out)
    print(f"GitHub write: {_control_status(controls.get('github'))}", file=out)
    print(f"File write:   {_control_status(controls.get('files'))}", file=out)
    print(f"Managed:      {_managed_status(controls.get('managed_comment'))}", file=out)
    return True


def _control_status(value: object) -> str:
    if not isinstance(value, dict):
        return "unknown"
    if value.get("mutates") is True:
        return str(value.get("status") or "yes")
    if value.get("requested") is True:
        return str(value.get("status") or "requested")
    status = str(value.get("status") or "")
    if status and status not in {"not_requested", "not_supported"}:
        return status
    return "no"


def _managed_status(value: object) -> str:
    if not isinstance(value, dict):
        return "unknown"
    if value.get("enabled") is True:
        return str(value.get("status") or "enabled")
    if value.get("requested") is True:
        return str(value.get("status") or "requested")
    return "no"
