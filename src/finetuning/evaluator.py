"""Evaluation metrics for OpenRabbit-Reviewer-v1.

:class:`Evaluator` scores a list of model-generated review comments against
their held-out references using three complementary metrics:

BLEU-4
    Corpus-level BLEU-4 via ``sacrebleu``. Reports on the [0, 100] scale.
    Captures n-gram overlap with a brevity penalty; standard in NLG evaluation.

ROUGE-L
    Mean ROUGE-L F1, precision, and recall across all examples via
    ``rouge-score``. Reports on the [0, 1] scale. Measures longest-common-
    subsequence overlap, which is more robust than BLEU for longer outputs.

Precision / Recall / F1
    Token-level precision and recall derived from ROUGE-L, averaged across
    examples.  These correspond to the Phase 5 targets:

        Precision  ≥ 0.70  (how many predicted tokens appear in the reference)
        Recall     ≥ 0.60  (how many reference tokens the prediction covers)
        FPR        < 0.10  (false-positive rate = 1 - precision)

All computation runs on plain strings.  No model, no GPU, and no torch import
are required -- the evaluator is the same for a real trained adapter and for
mock output in tests.

Usage::

    from finetuning.evaluator import Evaluator

    ev = Evaluator()
    report = ev.evaluate(hypotheses=generated_comments, references=gold_comments)
    print(f"BLEU-4:   {report.bleu4:.1f}")
    print(f"ROUGE-L:  {report.rouge_l:.3f}")
    print(f"Precision:{report.precision:.3f}  Recall:{report.recall:.3f}")
    print(f"FPR:      {report.false_positive_rate:.3f}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sacrebleu
from rouge_score import rouge_scorer

RougeName = Literal["rougeL", "rouge1", "rouge2"]


# ---------------------------------------------------------------------------
# EvalReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalReport:
    """Evaluation results for a batch of review comment predictions.

    Attributes
    ----------
    bleu4:
        Corpus BLEU-4 score on the [0, 100] scale (sacrebleu convention).
    rouge_l:
        Mean ROUGE-L F1 across all examples on [0, 1].
    precision:
        Mean ROUGE-L precision across all examples on [0, 1].
        Fraction of predicted tokens that appear in the reference.
    recall:
        Mean ROUGE-L recall across all examples on [0, 1].
        Fraction of reference tokens covered by the prediction.
    f1:
        Mean ROUGE-L F1 across all examples on [0, 1]. Equal to ``rouge_l``.
    false_positive_rate:
        ``1 - precision``. Fraction of predicted tokens absent from the
        reference (noise in the prediction).
    num_examples:
        Number of (hypothesis, reference) pairs evaluated.
    """

    bleu4: float
    rouge_l: float
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    num_examples: int


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class Evaluator:
    """Computes BLEU-4, ROUGE-L, and derived P/R/F1 metrics.

    Parameters
    ----------
    rouge_type:
        Which ROUGE variant to use for per-example scoring.
        Defaults to ``"rougeL"`` (longest common subsequence).
    use_stemmer:
        Whether to apply Porter stemming before ROUGE scoring.
        Disabled by default for deterministic, reproducible output.
    """

    def __init__(
        self,
        rouge_type: RougeName = "rougeL",
        use_stemmer: bool = False,
    ) -> None:
        self._rouge_type = rouge_type
        self._scorer = rouge_scorer.RougeScorer([rouge_type], use_stemmer=use_stemmer)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, hypotheses: list[str], references: list[str]) -> EvalReport:
        """Compute all metrics for the given prediction/reference pairs.

        Parameters
        ----------
        hypotheses:
            Model-generated review comments, one per example.
        references:
            Gold reference comments, one per example.

        Returns
        -------
        EvalReport
            Aggregated metrics over the full batch.

        Raises
        ------
        ValueError
            If *hypotheses* is empty or its length differs from *references*.
        """
        if not hypotheses:
            raise ValueError("hypotheses and references must not be empty")
        if len(hypotheses) != len(references):
            raise ValueError(
                f"hypotheses and references must have the same length "
                f"(got {len(hypotheses)} vs {len(references)})"
            )

        bleu4 = self._bleu4(hypotheses, references)
        precisions, recalls, f1s = self._rouge_scores(hypotheses, references)

        mean_p = sum(precisions) / len(precisions)
        mean_r = sum(recalls) / len(recalls)
        mean_f1 = sum(f1s) / len(f1s)

        return EvalReport(
            bleu4=bleu4,
            rouge_l=mean_f1,
            precision=mean_p,
            recall=mean_r,
            f1=mean_f1,
            false_positive_rate=1.0 - mean_p,
            num_examples=len(hypotheses),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _bleu4(self, hypotheses: list[str], references: list[str]) -> float:
        """Corpus BLEU-4 on [0, 100]. sacrebleu expects refs as list-of-lists.

        Clamps to [0, 100] to absorb the tiny floating-point overshoot
        (e.g. 100.00000000000004) that sacrebleu occasionally returns for
        perfect predictions.
        """
        result = sacrebleu.corpus_bleu(hypotheses, [references])
        return max(0.0, min(100.0, float(result.score)))

    def _rouge_scores(
        self, hypotheses: list[str], references: list[str]
    ) -> tuple[list[float], list[float], list[float]]:
        """Per-example ROUGE precision, recall, and F1."""
        precisions: list[float] = []
        recalls: list[float] = []
        f1s: list[float] = []
        for hyp, ref in zip(hypotheses, references, strict=True):
            scores = self._scorer.score(ref, hyp)
            rouge = scores[self._rouge_type]
            precisions.append(rouge.precision)
            recalls.append(rouge.recall)
            f1s.append(rouge.fmeasure)
        return precisions, recalls, f1s
