"""Local-first human review over existing private checkpoints; no API requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from p2_research.application.research.answer_evaluation import (
    DIMENSIONS,
    AnswerEvaluationError,
    HumanReview,
    ReviewLabel,
    build_report,
    load_frozen_cases,
    write_safe_report,
)
from p2_research.cli.answer_batch import _deserialize_run

ERROR_TYPES = (
    "unsupported_claim",
    "misattributed_citation",
    "missing_evidence",
    "factually_incorrect",
    "incomplete_answer",
    "unjustified_abstention",
    "invalid_format",
    "other",
)


def load_runs(private_dir: Path, gold: bytes) -> dict[str, Any]:
    """Only read known frozen IDs, never follow symlinks or untrusted glob names."""
    if private_dir.is_symlink() or not private_dir.is_dir():
        raise AnswerEvaluationError("invalid private directory")
    runs = {}
    for case in load_frozen_cases(gold):
        path = private_dir / (case["case_id"] + ".json")
        if path.is_symlink():
            raise AnswerEvaluationError("checkpoint symlink refused")
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("case_id") != case["case_id"]:
            raise AnswerEvaluationError("checkpoint case ID drift")
        run = _deserialize_run(record["run"])
        if run.result.question != case["question"]:
            raise AnswerEvaluationError("checkpoint question drift")
        runs[case["case_id"]] = run
    return runs


def review_template(runs: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "instructions": "Fill reviewer and all three labels (PASS/FAIL/UNCERTAIN) to submit a review. Leave null to skip. Evidence IDs must come from the checkpoint. No automated quality verdicts.",
        "cases": [
            {
                "case_id": cid,
                "reviewer": None,
                "answer_correctness": None,
                "claim_support": None,
                "citation_support": None,
                "errors": [],
                "notes": "",
            }
            for cid in sorted(runs)
        ],
    }


def parse_reviews(
    data: dict[str, Any], runs: dict[str, Any]
) -> tuple[dict[str, HumanReview], dict[str, list[str]]]:
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise AnswerEvaluationError("invalid review schema")
    reviews: dict[str, HumanReview] = {}
    errors: dict[str, list[str]] = {}
    seen: set[str] = set()
    for item in data["cases"]:
        if not isinstance(item, dict):
            raise AnswerEvaluationError("invalid review case")
        cid = item.get("case_id")
        if cid not in runs or cid in seen:
            raise AnswerEvaluationError("unknown or duplicate review case")
        seen.add(cid)
        values = [item.get("reviewer"), *(item.get(dim) for dim in DIMENSIONS)]
        if all(value is None for value in values):
            if item.get("errors") or item.get("notes"):
                raise AnswerEvaluationError("unsubmitted review contains findings")
            continue
        if not isinstance(item.get("reviewer"), str) or not item["reviewer"].strip():
            raise AnswerEvaluationError("reviewer is required")
        if not isinstance(item.get("notes", ""), str):
            raise AnswerEvaluationError("notes must be text")
        tags = item.get("errors", [])
        if (
            not isinstance(tags, list)
            or len(tags) != len(set(map(str, tags)))
            or any(not isinstance(tag, str) or tag not in ERROR_TYPES for tag in tags)
        ):
            raise AnswerEvaluationError("invalid error taxonomy")
        reviews[cid] = HumanReview(
            reviewer=item["reviewer"],
            answer_correctness=cast(ReviewLabel, item.get("answer_correctness")),
            claim_support=cast(ReviewLabel, item.get("claim_support")),
            citation_support=cast(ReviewLabel, item.get("citation_support")),
            notes=item.get("notes", ""),
        )
        errors[cid] = tags
    return reviews, errors


def aggregate(gold: bytes, runs: dict[str, Any], review_doc: dict[str, Any]) -> dict[str, Any]:
    reviews, taxonomy = parse_reviews(review_doc, runs)
    report = build_report(gold, runs, reviews)
    report["experiment"] = "s33-v03-human-answer-review"
    counts = {name: 0 for name in ERROR_TYPES}
    for tags in taxonomy.values():
        for tag in tags:
            counts[tag] += 1
    report["review_error_taxonomy"] = {name: count for name, count in counts.items() if count}
    report["review_coverage"] = {
        "reviewed": len(reviews),
        "available_runs": len(runs),
        "unreviewed": len(runs) - len(reviews),
    }
    # Scope-aware metrics: only delivered ANSWERED runs count as factual answers.
    # UNCERTAIN is not an observed FAIL and is not added to the PASS/FAIL denominator.
    # Abstentions and raw responses rejected by the output contract are separate scopes.
    delivered = [
        row
        for row in report["cases"]
        if row["observed_status"] == "ANSWERED" and row["human_review"] is not None
    ]
    dimension_rates = {}
    dimension_counts = {}
    for dim in DIMENSIONS:
        labels = [row["human_review"][dim] for row in delivered]
        passed = labels.count("PASS")
        failed = labels.count("FAIL")
        dimension_counts[dim] = {
            "pass": passed,
            "fail": failed,
            "uncertain": labels.count("UNCERTAIN"),
            "eligible": passed + failed,
        }
        dimension_rates[dim] = round(passed / (passed + failed), 6) if passed + failed else None
    abstentions = [
        row
        for row in report["cases"]
        if row["observed_disposition"] == "ABSTAINED" and row["human_review"] is not None
    ]
    abstention_labels = [row["human_review"]["answer_correctness"] for row in abstentions]
    report["scoped_human_metrics"] = {
        "delivered_answers": {
            "reviewed": len(delivered),
            "dimension_counts": dimension_counts,
            "pass_rates": dimension_rates,
        },
        "abstentions": {
            "reviewed": len(abstentions),
            "correct": abstention_labels.count("PASS"),
            "incorrect": abstention_labels.count("FAIL"),
            "uncertain": abstention_labels.count("UNCERTAIN"),
        },
        "failed_or_error_runs_reviewed": sum(
            row["observed_disposition"] == "FAILED_OR_ERROR" and row["human_review"] is not None
            for row in report["cases"]
        ),
        "interpretation": (
            "Delivered-answer PASS rates exclude abstentions and failed/errored runs. "
            "UNCERTAIN is excluded from PASS/FAIL denominators. "
            "For abstentions, answer_correctness is a proxy for abstention appropriateness; "
            "other dimensions are not scored as factual support. "
            "Reviews of rejected raw responses are diagnostic only."
        ),
    }
    # The legacy human_dimension_pass_rates field remains for schema compatibility;
    # use scoped_human_metrics for comparative reporting.
    # Human free-text notes remain local in the submitted review file.
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline human review of local answer checkpoints")
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.template) == bool(args.reviews):
        parser.error("choose exactly one: --template or --reviews")
    gold = args.gold.read_bytes()
    runs = load_runs(args.private_dir, gold)
    if args.template:
        write_safe_report(args.template, review_template(runs))
        print(json.dumps({"available_runs": len(runs), "reviews_completed": 0}))
        return
    if args.output is None:
        parser.error("--output is required with --reviews")
    doc = json.loads(args.reviews.read_text(encoding="utf-8"))
    report = aggregate(gold, runs, doc)
    write_safe_report(args.output, report)
    print(
        json.dumps(
            {"available_runs": len(runs), "reviews_completed": report["human_reviewed_cases"]}
        )
    )


if __name__ == "__main__":
    main()
