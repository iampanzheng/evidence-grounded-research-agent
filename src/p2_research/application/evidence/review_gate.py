"""Fail-closed preliminary annotation review contract (no inferred approvals)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .annotations import AnnotationError, validate_pilot


class ReviewGateError(ValueError):
    """Review records are ambiguous or misbound to frozen evidence."""


def validate_preaudit(
    review: dict[str, Any], pilot: dict[str, Any], chunks: bytes
) -> dict[str, int]:
    """Require exact pilot binding, recommendations, and explicit human decision metadata."""
    try:
        validate_pilot(pilot, chunks)
    except AnnotationError as exc:
        raise ReviewGateError(f"invalid pilot evidence: {exc}") from exc
    if (
        review.get("schema_version") != 1
        or review.get("chunks_sha256") != pilot["corpus"]["chunks_sha256"]
    ):
        raise ReviewGateError("review baseline mismatch")
    if review.get("approval_policy") != "recommendations_are_not_approvals":
        raise ReviewGateError("review policy mismatch")
    expected = {case["case_id"] for case in pilot["cases"]}
    entries = review.get("review_cases")
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ReviewGateError("review case count mismatch")
    canonical = {case["case_id"]: case for case in pilot["cases"]}
    seen: set[str] = set()
    approved = 0
    for value in entries:
        if not isinstance(value, dict):
            raise ReviewGateError("invalid review entry")
        case_id = value.get("case_id")
        if not isinstance(case_id, str) or case_id not in expected or case_id in seen:
            raise ReviewGateError("invalid or duplicate case id")
        seen.add(case_id)
        for field in ("question", "expected_answer_notes", "language", "answerability"):
            if value.get(field) != canonical[case_id].get(field):
                raise ReviewGateError(f"review content drift: {field}")
        if value.get("recommendation") not in {
            "recommended_approve",
            "needs_revision",
            "recommended_reject",
        }:
            raise ReviewGateError("invalid recommendation")
        decision = value.get("human_decision")
        if decision is None:
            if any(
                value.get(key) is not None
                for key in ("human_reviewer", "human_reviewed_at", "human_notes")
            ):
                raise ReviewGateError("unsigned review must have null metadata")
            continue
        if decision not in {"approve", "revise", "reject"}:
            raise ReviewGateError("invalid human decision")
        for field in ("human_reviewer", "human_reviewed_at", "human_notes"):
            text = value.get(field)
            if not isinstance(text, str) or not text.strip():
                raise ReviewGateError(f"missing human review metadata: {field}")
        if decision == "approve":
            approved += 1
    cross = review.get("cross_document_candidate")
    if not isinstance(cross, dict) or cross.get("case_type") != "cross_doc":
        raise ReviewGateError("cross-document candidate missing")
    evidence = cross.get("evidence")
    if (
        not isinstance(evidence, list)
        or len({x.get("document_id") for x in evidence if isinstance(x, dict)}) < 2
    ):
        raise ReviewGateError("cross-document candidate needs two distinct sources")
    if cross.get("human_decision") is not None:
        raise ReviewGateError("supplemental candidate requires independent review workflow")
    return {
        "cases": len(entries),
        "human_approved": approved,
        "pending": sum(x["human_decision"] is None for x in entries),
    }


def validate_preaudit_files(
    review_path: Path, pilot_path: Path, chunks_path: Path
) -> dict[str, int]:
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
        chunks = chunks_path.read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewGateError("unable to read review inputs") from exc
    if not isinstance(review, dict) or not isinstance(pilot, dict):
        raise ReviewGateError("invalid input object")
    try:
        return validate_preaudit(review, pilot, chunks)
    except AnnotationError as exc:
        raise ReviewGateError(f"invalid pilot evidence: {exc}") from exc
