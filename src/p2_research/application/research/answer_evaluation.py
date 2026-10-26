"""S3.3 answer evaluation contract: automated checks never imply semantic support."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from p2_research.application.research.llm import GenerationRun
from p2_research.application.retrieval.benchmark import GOLD_SHA256
from p2_research.domain.models import ResearchStatus

ReviewLabel = Literal["PASS", "PARTIAL", "FAIL", "UNCERTAIN", "NOT_APPLICABLE"]
DIMENSIONS = ("answer_correctness", "claim_support", "citation_support")
# Approved, immutable same-corpus held-out Gold v1. Other files remain rejected.
HELDOUT_GOLD_SHA256 = "80a5e8c2bd4b5f4d5f24c70284286f54b3d100d1bda0b313a75c1fde99590423"
S35_FINAL_GOLD_SHA256 = "5810bff70623068eb9fb25b11e7f70bf7bdef1af87846e4e719d0235392338f8"
S37_INDEPENDENT_GOLD_SHA256 = "546850e9d79893923248c1f221e6ba1bcfe978fb75aadd4244acceaa4800f796"
FROZEN_GOLD_CASE_COUNTS = {
    GOLD_SHA256: 10,
    HELDOUT_GOLD_SHA256: 6,
    S35_FINAL_GOLD_SHA256: 8,
    S37_INDEPENDENT_GOLD_SHA256: 8,
}
CITATION_PROVENANCE_VALUES = (
    "MODEL_INLINE",
    "SYSTEM_APPENDED_SINGLE_SENTENCE",
    "ABSTAINED",
    "VALIDATION_FAILED",
    "PROVIDER_ERROR",
    "NOT_OBSERVED",
)


class AnswerEvaluationError(ValueError):
    """Invalid evaluation input or attempted ungrounded scoring."""


@dataclass(frozen=True)
class HumanReview:
    """A review is only authoritative when explicitly submitted by a human."""

    reviewer: str
    answer_correctness: ReviewLabel
    claim_support: ReviewLabel
    citation_support: ReviewLabel
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.reviewer.strip() or any(
            getattr(self, dimension)
            not in ("PASS", "PARTIAL", "FAIL", "UNCERTAIN", "NOT_APPLICABLE")
            for dimension in DIMENSIONS
        ):
            raise AnswerEvaluationError("invalid human review")


def load_frozen_cases(gold_bytes: bytes) -> tuple[dict[str, Any], ...]:
    digest = hashlib.sha256(gold_bytes).hexdigest()
    if digest not in FROZEN_GOLD_CASE_COUNTS:
        raise AnswerEvaluationError("GOLD_DATASET_DRIFT")
    gold = json.loads(gold_bytes)
    cases = gold["cases"]
    if len(cases) != FROZEN_GOLD_CASE_COUNTS[digest] or len({c["case_id"] for c in cases}) != len(
        cases
    ):
        raise AnswerEvaluationError("invalid frozen Gold cases")
    return tuple(cases)


def evaluate_case(
    gold_case: dict[str, Any], run: GenerationRun, review: HumanReview | None = None
) -> dict[str, Any]:
    """Grade mechanical contract only; no automated entailment or correctness grade."""
    if run.result.question != gold_case["question"]:
        raise AnswerEvaluationError("question does not match frozen case")
    expected_answerable = gold_case["answerability"] == "answerable"
    status = run.result.status
    known = {e.citation_id for e in run.result.evidence}
    valid_citations = len(known) == len(run.result.evidence) and set(run.result.citations) <= known
    abstained = status == ResearchStatus.INSUFFICIENT_EVIDENCE
    # Status-only classification is not sufficient to prove semantic abstention correctness.
    disposition = (
        "ABSTAINED"
        if abstained
        else "ANSWERED"
        if status == ResearchStatus.ANSWERED
        else "FAILED_OR_ERROR"
    )
    return {
        "case_id": gold_case["case_id"],
        "language": gold_case["language"],
        "expected_answerability": gold_case["answerability"],
        "observed_status": status.value,
        "observed_disposition": disposition,
        "mechanical_citation_integrity": valid_citations,
        "referenced_evidence_count": len(run.result.citations),
        "retrieved_evidence_count": len(run.result.evidence),
        "structural_outcome_matches_answerability": (
            status == ResearchStatus.ANSWERED if expected_answerable else abstained
        ),
        "error_category": run.error_category,
        "citation_provenance": run.citation_provenance,
        "model": run.model,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "latency_ms": round(run.latency_ms, 3),
        "human_review": (
            {"reviewer": review.reviewer, **{k: getattr(review, k) for k in DIMENSIONS}}
            if review
            else None
        ),
        "semantic_quality": "HUMAN_REVIEWED" if review else "NOT_REVIEWED",
    }


def summarize_citation_provenance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Report citation origin separately from structure and semantic correctness.

    Rate denominators exclude legacy unknown provenance and non-answered cases.
    No provenance category on its own is a factual support score.
    """
    counts = {name: 0 for name in CITATION_PROVENANCE_VALUES}
    for row in rows:
        provenance = row.get("citation_provenance", "NOT_OBSERVED")
        if provenance not in counts:
            raise AnswerEvaluationError("unknown citation provenance")
        counts[provenance] += 1

    answered = [row for row in rows if row["observed_disposition"] == "ANSWERED"]
    known_answered = [
        row
        for row in answered
        if row.get("citation_provenance") in ("MODEL_INLINE", "SYSTEM_APPENDED_SINGLE_SENTENCE")
    ]
    model_inline = sum(row["citation_provenance"] == "MODEL_INLINE" for row in known_answered)
    appended = len(known_answered) - model_inline
    reviewed = [row for row in known_answered if row.get("human_review")]
    return {
        "counts_by_origin": counts,
        "answered_cases": len(answered),
        "answered_with_known_citation_origin": len(known_answered),
        "model_inline_share_of_known_answered": (
            round(model_inline / len(known_answered), 6) if known_answered else None
        ),
        "system_appended_share_of_known_answered": (
            round(appended / len(known_answered), 6) if known_answered else None
        ),
        "human_reviewed_answered_with_known_origin": len(reviewed),
        "human_dimension_pass_rates_by_origin": {
            origin: {
                "reviewed_cases": len(group),
                "pass_rates": {
                    dim: (
                        round(
                            sum(
                                row["human_review"][dim] == "PASS"
                                for row in group
                                if row["human_review"][dim] != "NOT_APPLICABLE"
                            )
                            / sum(row["human_review"][dim] != "NOT_APPLICABLE" for row in group),
                            6,
                        )
                        if any(row["human_review"][dim] != "NOT_APPLICABLE" for row in group)
                        else None
                    )
                    for dim in DIMENSIONS
                },
            }
            for origin in ("MODEL_INLINE", "SYSTEM_APPENDED_SINGLE_SENTENCE")
            for group in [[row for row in reviewed if row["citation_provenance"] == origin]]
        },
        "caveat": (
            "Citation origin tracks formatting attribution only; system-appended references "
            "are not model-generated inline citations. Neither type establishes factual "
            "correctness, claim entailment, or citation support without human review. "
            "Legacy NOT_OBSERVED cases are excluded from provenance rate denominators."
        ),
    }


def build_report(
    gold_bytes: bytes,
    runs: dict[str, GenerationRun],
    reviews: dict[str, HumanReview] | None = None,
) -> dict[str, Any]:
    cases = load_frozen_cases(gold_bytes)
    known_ids = {case["case_id"] for case in cases}
    if not set(runs) <= known_ids or (reviews and not set(reviews) <= known_ids):
        raise AnswerEvaluationError("unknown evaluation case ID")
    reviews = reviews or {}
    if not set(reviews) <= set(runs):
        raise AnswerEvaluationError("review without model output")
    results = [
        evaluate_case(case, runs[case["case_id"]], reviews.get(case["case_id"]))
        for case in cases
        if case["case_id"] in runs
    ]
    graded = [row for row in results if row["human_review"]]
    applicable = {
        dim: [row for row in graded if row["human_review"][dim] != "NOT_APPLICABLE"]
        for dim in DIMENSIONS
    }
    return {
        "schema_version": 1,
        "experiment": "s33-v01-answer-evaluation-contract",
        "gold_sha256": hashlib.sha256(gold_bytes).hexdigest(),
        "case_total": len(cases),
        "executed_cases": len(results),
        "human_reviewed_cases": len(graded),
        "automated_structural_matches": sum(
            bool(r["structural_outcome_matches_answerability"]) for r in results
        ),
        "human_dimension_pass_rates": {
            dim: (
                round(
                    sum(r["human_review"][dim] == "PASS" for r in applicable[dim])
                    / len(applicable[dim]),
                    6,
                )
                if applicable[dim]
                else None
            )
            for dim in DIMENSIONS
        },
        "human_dimension_denominators": {dim: len(applicable[dim]) for dim in DIMENSIONS},
        "human_dimension_counts": {
            dim: {
                label: sum(r["human_review"][dim] == label for r in graded)
                for label in ("PASS", "PARTIAL", "FAIL", "UNCERTAIN", "NOT_APPLICABLE")
            }
            for dim in DIMENSIONS
        },
        "citation_provenance_summary": summarize_citation_provenance(results),
        "cases": results,
        "contains_source_text": False,
        "caveat": (
            "Status/citation integrity is not factual correctness or entailment. "
            "Human PASS rates are over reviewed runs only; no LLM output is fabricated."
        ),
    }


def write_safe_report(path: Path, report: dict[str, Any]) -> None:
    if path.is_symlink():
        raise AnswerEvaluationError("refusing symlink output")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
