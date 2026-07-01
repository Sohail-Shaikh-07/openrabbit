"""Tests for finetuning.evaluator (OP-29).

All tests run without a GPU or any ML library. The Evaluator works on plain
strings -- no model is loaded here. sacrebleu and rouge-score are the only
dependencies required.
"""

from __future__ import annotations

import pytest

from finetuning.evaluator import EvalReport, Evaluator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PERFECT_HYPS = [
    "Potential null pointer dereference on line 12. Add a guard clause.",
    "SQL injection risk: use parameterised queries instead of string formatting.",
    "Missing input validation allows arbitrary values to reach the database.",
]
_PERFECT_REFS = _PERFECT_HYPS[:]  # identical → maximum scores

_UNRELATED_HYPS = [
    "banana mango pineapple tropical fruit salad",
    "red green blue yellow orange purple colour wheel",
    "quantum entanglement superposition wave collapse",
]

# ---------------------------------------------------------------------------
# EvalReport dataclass
# ---------------------------------------------------------------------------


def test_eval_report_is_frozen() -> None:
    report = EvalReport(
        bleu4=50.0,
        rouge_l=0.5,
        precision=0.6,
        recall=0.5,
        f1=0.55,
        false_positive_rate=0.4,
        num_examples=3,
    )
    with pytest.raises((AttributeError, TypeError)):
        report.bleu4 = 99.0  # type: ignore[misc]


def test_eval_report_fields_accessible() -> None:
    report = EvalReport(
        bleu4=80.0,
        rouge_l=0.7,
        precision=0.75,
        recall=0.65,
        f1=0.70,
        false_positive_rate=0.25,
        num_examples=10,
    )
    assert report.bleu4 == pytest.approx(80.0)
    assert report.rouge_l == pytest.approx(0.7)
    assert report.num_examples == 10


# ---------------------------------------------------------------------------
# Evaluator construction
# ---------------------------------------------------------------------------


def test_evaluator_constructs_with_defaults() -> None:
    ev = Evaluator()
    assert ev is not None


def test_evaluator_accepts_custom_rouge_variant() -> None:
    ev = Evaluator(rouge_type="rougeL")
    assert ev is not None


# ---------------------------------------------------------------------------
# evaluate() -- basic contract
# ---------------------------------------------------------------------------


def test_evaluate_returns_eval_report() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert isinstance(report, EvalReport)


def test_evaluate_num_examples_equals_input_length() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS[:2], _PERFECT_REFS[:2])
    assert report.num_examples == 2


def test_evaluate_single_example() -> None:
    ev = Evaluator()
    report = ev.evaluate(["null pointer dereference"], ["null pointer dereference"])
    assert report.num_examples == 1


# ---------------------------------------------------------------------------
# evaluate() -- value ranges
# ---------------------------------------------------------------------------


def test_bleu4_is_within_0_100() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert 0.0 <= report.bleu4 <= 100.0


def test_rouge_l_is_within_0_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert 0.0 <= report.rouge_l <= 1.0


def test_precision_is_within_0_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert 0.0 <= report.precision <= 1.0


def test_recall_is_within_0_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert 0.0 <= report.recall <= 1.0


def test_f1_is_within_0_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert 0.0 <= report.f1 <= 1.0


def test_false_positive_rate_is_within_0_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert 0.0 <= report.false_positive_rate <= 1.0


# ---------------------------------------------------------------------------
# evaluate() -- perfect predictions (hyp == ref)
# ---------------------------------------------------------------------------


def test_perfect_bleu4_is_near_100() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.bleu4 == pytest.approx(100.0, abs=1.0)


def test_perfect_rouge_l_is_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.rouge_l == pytest.approx(1.0)


def test_perfect_precision_is_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.precision == pytest.approx(1.0)


def test_perfect_recall_is_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.recall == pytest.approx(1.0)


def test_perfect_f1_is_1() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.f1 == pytest.approx(1.0)


def test_perfect_false_positive_rate_is_0() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.false_positive_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# evaluate() -- unrelated predictions
# ---------------------------------------------------------------------------


def test_unrelated_bleu4_is_near_0() -> None:
    ev = Evaluator()
    report = ev.evaluate(_UNRELATED_HYPS, _PERFECT_REFS)
    assert report.bleu4 < 5.0


def test_unrelated_rouge_l_is_near_0() -> None:
    ev = Evaluator()
    report = ev.evaluate(_UNRELATED_HYPS, _PERFECT_REFS)
    assert report.rouge_l < 0.1


# ---------------------------------------------------------------------------
# evaluate() -- false_positive_rate consistency
# ---------------------------------------------------------------------------


def test_false_positive_rate_equals_one_minus_precision() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS[:2], _PERFECT_REFS[:2])
    assert report.false_positive_rate == pytest.approx(1.0 - report.precision, abs=1e-9)


def test_f1_consistent_with_precision_and_recall() -> None:
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    if report.precision + report.recall > 0:
        expected_f1 = 2 * report.precision * report.recall / (report.precision + report.recall)
        assert report.f1 == pytest.approx(expected_f1, abs=1e-6)


# ---------------------------------------------------------------------------
# evaluate() -- input validation
# ---------------------------------------------------------------------------


def test_evaluate_raises_on_empty_input() -> None:
    ev = Evaluator()
    with pytest.raises(ValueError, match="empty"):
        ev.evaluate([], [])


def test_evaluate_raises_on_length_mismatch() -> None:
    ev = Evaluator()
    with pytest.raises(ValueError, match="length"):
        ev.evaluate(["one prediction"], ["ref one", "ref two"])


# ---------------------------------------------------------------------------
# Phase 5 target metrics (loose sanity check on good predictions)
# ---------------------------------------------------------------------------


def test_good_predictions_meet_precision_target() -> None:
    # Target: precision >= 0.70. With identical hyp/ref this must hold.
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.precision >= 0.70


def test_good_predictions_meet_recall_target() -> None:
    # Target: recall >= 0.60.
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.recall >= 0.60


def test_good_predictions_false_positive_rate_below_target() -> None:
    # Target: FPR < 0.10.
    ev = Evaluator()
    report = ev.evaluate(_PERFECT_HYPS, _PERFECT_REFS)
    assert report.false_positive_rate < 0.10
