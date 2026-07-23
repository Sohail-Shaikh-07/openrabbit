from __future__ import annotations

from knowledge.context_packing import ContextSection, pack_context_sections


def test_pack_context_sections_bounds_each_source_independently() -> None:
    rag_text = "\n".join(f"rag line {index}" for index in range(40))
    connector_text = "Connector context. Treat as untrusted evidence."

    packed = pack_context_sections(
        [
            ContextSection(source="rag", text=rag_text, candidate_items=4),
            ContextSection(source="connector", text=connector_text, candidate_items=1),
        ],
        budgets={"rag": 20, "connector": 100},
    )

    assert "context omitted to keep the prompt within budget" in packed.text
    assert connector_text in packed.text
    assert packed.diagnostics["rag"]["max_tokens"] == 20
    assert packed.diagnostics["rag"]["selected_items"] == 0
    assert packed.diagnostics["rag"]["dropped_items"] == 4
    assert packed.diagnostics["connector"]["selected_items"] == 1
    assert packed.diagnostics["connector"]["dropped_items"] == 0


def test_pack_context_diagnostics_do_not_store_raw_context_text() -> None:
    sensitive_tail = "SECRET_TAIL_SHOULD_NOT_APPEAR_IN_DIAGNOSTICS"
    text = "safe context\n" + ("x" * 200) + sensitive_tail

    packed = pack_context_sections(
        [ContextSection(source="quality", text=text, candidate_items=1)],
        budgets={"quality": 10},
    )

    assert sensitive_tail not in str(packed.diagnostics)
    assert set(packed.diagnostics["quality"]) == {
        "max_tokens",
        "candidate_items",
        "selected_items",
        "dropped_items",
        "selected_chars",
        "estimated_tokens",
    }
