"""Regression tests for the copyable GitHub Actions recipe."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / "examples" / "github-actions" / "openrabbit-review.yml"
_MAINTENANCE_WORKFLOW = _ROOT / "examples" / "github-actions" / "openrabbit-maintenance.yml"


def _workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_github_actions_recipe_uses_minimal_pr_trigger() -> None:
    text = _workflow_text()

    assert "pull_request:" in text
    assert "pull_request_target" not in text
    assert "contents: read" in text
    assert "pull-requests: write" in text


def test_github_actions_recipe_targets_self_hosted_openrabbit_runner() -> None:
    text = _workflow_text()

    assert "runs-on: [self-hosted, linux, openrabbit]" in text


def test_github_actions_recipe_defaults_manual_runs_to_dry_run() -> None:
    text = _workflow_text()

    assert "dry_run:" in text
    assert "default: true" in text
    assert "OPENRABBIT_DRY_RUN" in text
    assert "--dry-run" in text


def test_github_actions_recipe_keeps_qdrant_optional() -> None:
    text = _workflow_text()

    assert "openrabbit index --workspace . --health" in text
    assert text.count("continue-on-error: true") >= 2


def test_maintenance_workflow_is_manual_and_permission_scoped() -> None:
    text = _MAINTENANCE_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "pull_request_target" not in text
    assert "contents: read" in text
    assert "pull-requests: read" in text
    assert "issues: write" in text
    assert "apply_labels:" in text
    assert "args+=(--apply)" in text


def test_maintenance_workflow_covers_all_maintenance_commands() -> None:
    text = _MAINTENANCE_WORKFLOW.read_text(encoding="utf-8")

    for claim in (
        "- labels",
        "- changelog",
        "- docs",
        "- similar-issues",
        "openrabbit docs",
        "openrabbit similar-issues",
        "args=(labels",
        "args=(changelog",
        "require_pr",
    ):
        assert claim in text
