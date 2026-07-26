"""Cross-command regressions for v1.8 interactive PR workflows."""

from __future__ import annotations

import io
import json
from collections.abc import Callable

from cli.commands.ask import render_answer_json
from cli.commands.describe import render_description_json
from cli.commands.improve import render_improvements_json
from cli.commands.labels import render_label_proposals_json
from cli.commands.pr_answer import ANSWER_MARKER, format_pr_answer
from cli.commands.pr_summary import SUMMARY_MARKER, format_pr_walkthrough_summary


def _render_json(
    renderer: Callable[[dict[str, object], io.StringIO], None],
    summary: dict[str, object],
) -> dict[str, object]:
    out = io.StringIO()
    renderer(summary, out)
    return json.loads(out.getvalue())


def _assert_no_prompt_keys(value: object) -> None:
    forbidden = {"prompt", "raw_prompt", "model_prompt", "prompt_context"}
    if isinstance(value, dict):
        assert forbidden.isdisjoint(value)
        for child in value.values():
            _assert_no_prompt_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_prompt_keys(child)


def test_interactive_json_contracts_are_stable_and_privacy_bounded() -> None:
    cases = [
        (
            render_description_json,
            {
                "schema_version": "1.0",
                "command": "describe",
                "repo": "o/r",
                "number": 42,
                "description": {"summary": "Search now accepts a query."},
                "publish_status": "read_only",
                "managed_summary": {
                    "enabled": False,
                    "status": "read_only",
                    "marker": SUMMARY_MARKER,
                    "comment_id": None,
                    "comment_url": None,
                },
            },
            ("description", "managed_summary", "publish_status"),
        ),
        (
            render_answer_json,
            {
                "schema_version": "1.0",
                "command": "ask",
                "repo": "o/r",
                "number": 42,
                "question": "Is this safe?",
                "answer": {"answer": "The changed line is bounded."},
                "ask_focus": {
                    "enabled": True,
                    "file": "src/search.py",
                    "line": 2,
                    "changed_line": "+2     return query",
                    "nearby_diff": ["+2     return query"],
                },
                "publish_status": "read_only",
                "managed_answer": {
                    "enabled": False,
                    "status": "read_only",
                    "marker": ANSWER_MARKER,
                    "comment_id": None,
                    "comment_url": None,
                },
            },
            ("answer", "ask_focus", "managed_answer", "publish_status"),
        ),
        (
            render_improvements_json,
            {
                "schema_version": "1.0",
                "command": "improve",
                "repo": "o/r",
                "number": 42,
                "suggestions_count": 1,
                "suggestion_quality": {
                    "raw_suggestions_count": 2,
                    "grounded_suggestions_count": 1,
                    "kept_suggestions_count": 1,
                    "dropped_suggestions_count": 1,
                    "dropped_reasons": {"ungrounded": 1},
                },
                "publish_status": "dry_run",
                "suggestions": [
                    {
                        "file": "src/search.py",
                        "line": 2,
                        "title": "Validate query",
                        "reason": "The changed line accepts input.",
                        "suggestion": "Reject empty queries before returning.",
                        "fix": "",
                    }
                ],
            },
            ("suggestions", "suggestion_quality", "publish_status"),
        ),
        (
            render_label_proposals_json,
            {
                "schema_version": "1.0",
                "command": "labels",
                "repo": "o/r",
                "number": 42,
                "proposal_count": 1,
                "mutates_github": False,
                "label_proposals": [
                    {
                        "name": "tests",
                        "confidence": 0.52,
                        "reason": "Changed files touch tests areas.",
                        "signals": ["path:tests/cli/test_search.py"],
                        "exists_in_repository": True,
                    }
                ],
            },
            ("label_proposals", "mutates_github", "proposal_count"),
        ),
    ]

    for renderer, summary, required_fields in cases:
        payload = _render_json(renderer, summary)

        assert payload["schema_version"] == "1.0"
        assert payload["command"] == summary["command"]
        for field in required_fields:
            assert field in payload
        _assert_no_prompt_keys(payload)

    assert cases[-1][1]["mutates_github"] is False


def test_managed_pr_comment_bodies_keep_stable_markers_and_slash_commands() -> None:
    summary_body = format_pr_walkthrough_summary(
        {
            "state": "open",
            "head_sha": "abcdef012345",
            "files_changed": 1,
            "context_loaded": True,
            "description": {
                "summary": "Search now accepts a query.",
                "testing_focus": ["Run search tests."],
            },
        }
    )
    answer_body = format_pr_answer(
        {
            "state": "open",
            "head_sha": "abcdef012345",
            "files_changed": 1,
            "context_loaded": True,
            "question": "Is this safe?",
            "answer": {
                "answer": "Yes, the line is bounded by changed-line validation.",
                "follow_up_checks": ["Run ask command tests."],
            },
        }
    )

    assert summary_body.startswith(f"{SUMMARY_MARKER}\n")
    assert ANSWER_MARKER in answer_body
    assert "/openrabbit review" in summary_body
    assert "/openrabbit ask <question>" in summary_body
    assert "@openrabbit" not in summary_body
    assert "@openrabbit" not in answer_body
