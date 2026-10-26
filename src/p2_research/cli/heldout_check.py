"""Offline held-out candidate integrity check; does not approve or evaluate candidates."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def normalized_question(text: str) -> str:
    return re.sub(r"[^\w]+", "", text.casefold(), flags=re.UNICODE)


def validate_candidates(
    candidate: dict[str, Any], pilot: dict[str, Any], corpus: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if candidate.get("status") != "CANDIDATE_NOT_GOLD":
        errors.append("candidate_status_must_remain_pending")
    if candidate.get("corpus") != pilot.get("corpus"):
        errors.append("corpus_contract_mismatch")
    questions = {normalized_question(x["question"]) for x in pilot["cases"]}
    known_ids: set[str] = set()
    known_questions: set[str] = set()
    chunks = {x["chunk_id"]: x for x in corpus["chunks"]}
    for item in candidate.get("cases", []):
        case_id = item["case_id"]
        q = normalized_question(item["question"])
        if case_id in known_ids or q in known_questions:
            errors.append(f"{case_id}:duplicate_candidate")
        known_ids.add(case_id)
        known_questions.add(q)
        if q in questions or case_id in {x["case_id"] for x in pilot["cases"]}:
            errors.append(f"{case_id}:pilot_question_overlap")
        if (
            item.get("annotation_status") != "pending_human_review"
            or item.get("reviewer") is not None
        ):
            errors.append(f"{case_id}:requires_human_review")
        for ev in item.get("gold_evidence", []):
            chunk = chunks.get(ev["chunk_id"])
            if chunk is None:
                errors.append(f"{case_id}:missing_chunk")
                continue
            for key in ("document_id", "source_path", "source_sha256"):
                if chunk[key] != ev[key]:
                    errors.append(f"{case_id}:{key}_mismatch")
            offset = ev["start_byte"] - chunk["start_byte"]
            size = ev["end_byte"] - ev["start_byte"]
            data = chunk["text"].encode("utf-8")
            if offset < 0 or size <= 0 or offset + size > len(data):
                errors.append(f"{case_id}:quote_out_of_bounds")
            elif hashlib.sha256(data[offset : offset + size]).hexdigest() != ev["quote_sha256"]:
                errors.append(f"{case_id}:quote_hash_mismatch")
    if not candidate.get("cases"):
        errors.append("no_candidates")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates", type=Path, default=Path("evaluation/datasets/heldout-candidates-v0.1.json")
    )
    parser.add_argument(
        "--pilot", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json")
    )
    parser.add_argument(
        "--corpus", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    args = parser.parse_args()
    cand = json.loads(args.candidates.read_text(encoding="utf-8"))
    pilot = json.loads(args.pilot.read_text(encoding="utf-8"))
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    digest = hashlib.sha256(args.pilot.read_bytes()).hexdigest()
    problems = validate_candidates(cand, pilot, corpus)
    if cand.get("source_pilot_sha256") != digest:
        problems.append("source_pilot_hash_mismatch")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        raise SystemExit(1)
    print(
        f"PASS: {len(cand['cases'])} candidates; source references verified; human signoff pending"
    )


if __name__ == "__main__":
    main()
