"""Regression tests for the copyable GitHub Actions recipe."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / "examples" / "github-actions" / "openrabbit-review.yml"


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
