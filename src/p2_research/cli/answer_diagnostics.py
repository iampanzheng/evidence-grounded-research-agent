"""Metadata-only failure categories from an existing public-safe batch report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("experiment") != "s33-v02-private-answer-collection":
        raise ValueError("unsupported answer batch report")
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("invalid cases list")
    categories: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    failed_ids: dict[str, list[str]] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            raise ValueError("invalid case metadata")
        status = case.get("observed_status")
        category = case.get("error_category")
        if not isinstance(status, str) or (category is not None and not isinstance(category, str)):
            raise ValueError("invalid status metadata")
        statuses[status] += 1
        if category is not None:
            categories[category] += 1
            failed_ids.setdefault(category, []).append(case["case_id"])
    return {
        "schema_version": 1,
        "type": "metadata_only_answer_diagnostics",
        "case_count": len(cases),
        "status_counts": dict(sorted(statuses.items())),
        "error_counts": dict(sorted(categories.items())),
        "error_case_ids": {key: sorted(ids) for key, ids in sorted(failed_ids.items())},
        "raw_model_response_available": False,
        "caveat": (
            "This report contains no raw model response. Existing checkpoints store normalized "
            "outcomes, so the exact failed JSON cannot be reconstructed."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize answer failures without source text")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary = summarize(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({"case_count": summary["case_count"], "error_counts": summary["error_counts"]})
    )


if __name__ == "__main__":
    main()
