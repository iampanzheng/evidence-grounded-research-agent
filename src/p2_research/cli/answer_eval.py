"""Create the independent answer-evaluation review plan (no live API calls)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from p2_research.application.research.answer_evaluation import load_frozen_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="S3.3 answer evaluation review plan")
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = load_frozen_cases(args.gold.read_bytes())
    plan = {
        "schema_version": 1,
        "stage": "s33-v01-answer-review-plan",
        "cases": [
            {
                "case_id": c["case_id"],
                "language": c["language"],
                "answerability": c["answerability"],
                "case_type": c["case_type"],
                "human_review": None,
            }
            for c in cases
        ],
        "quality_dimensions": ["answer_correctness", "claim_support", "citation_support"],
        "allowed_review_labels": ["PASS", "FAIL", "UNCERTAIN"],
        "warning": "Review plan only. No live outputs evaluated; no answer-quality scores computed.",
    }
    if args.output.is_symlink():
        raise ValueError("refusing symlink output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"case_total": len(cases), "human_reviews_completed": 0}))


if __name__ == "__main__":
    main()
