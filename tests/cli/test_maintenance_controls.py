"""Tests for shared maintenance workflow control metadata."""

from __future__ import annotations

import io

from cli.commands.maintenance_controls import (
    build_workflow_controls,
    read_only_workflow_controls,
    render_workflow_control_lines,
)


def test_read_only_workflow_controls_are_stable() -> None:
    controls = read_only_workflow_controls(
        required_permissions=("pull_requests:read", "issues:read", "issues:read")
    )

    assert controls == {
        "mode": "read_only",
        "dry_run": True,
        "required_permissions": ["pull_requests:read", "issues:read"],
        "github": {
            "requested": False,
            "mutates": False,
            "operation": "none",
            "status": "not_requested",
        },
        "files": {
            "requested": False,
            "mutates": False,
            "operation": "none",
            "status": "not_supported",
        },
        "managed_comment": {
            "requested": False,
            "enabled": False,
            "operation": "none",
            "status": "not_supported",
        },
    }


def test_apply_workflow_controls_capture_requested_write_and_permissions() -> None:
    controls = build_workflow_controls(
        mode="apply",
        required_permissions=("pull_requests:read", "issues:read", "issues:write"),
        github_write_requested=True,
        github_write_operation="label_application",
        github_write_status="applied",
        mutates_github=True,
    )

    assert controls["mode"] == "apply"
    assert controls["dry_run"] is False
    assert controls["required_permissions"] == [
        "pull_requests:read",
        "issues:read",
        "issues:write",
    ]
    assert controls["github"] == {
        "requested": True,
        "mutates": True,
        "operation": "label_application",
        "status": "applied",
    }
    assert controls["files"]["mutates"] is False
    assert controls["managed_comment"]["status"] == "not_supported"


def test_render_workflow_control_lines_prints_compact_summary() -> None:
    out = io.StringIO()
    summary = {
        "workflow_controls": build_workflow_controls(
            mode="dry_run",
            required_permissions=("pull_requests:read",),
            github_write_operation="label_application",
            github_write_status="dry_run",
        )
    }

    rendered = render_workflow_control_lines(summary, out)

    assert rendered is True
    text = out.getvalue()
    assert "Mode:" in text
    assert "dry_run" in text
    assert "GitHub write: dry_run" in text
    assert "File write:   no" in text
    assert "Managed:      no" in text


def test_render_workflow_control_lines_skips_legacy_summaries() -> None:
    out = io.StringIO()

    assert render_workflow_control_lines({}, out) is False
    assert out.getvalue() == ""
