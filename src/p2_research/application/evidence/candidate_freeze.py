"""Fail-closed editorial candidate validation; direction approval is never gold approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .annotations import AnnotationError, validate_pilot


class CandidateGateError(ValueError):
    """Candidate or human-review scope cannot be trusted."""


def validate_candidate(candidate: dict[str, Any], source: bytes, chunks: bytes) -> dict[str, int]:
    """Validate revised evidence and forbid candidate promotion without content review."""
    if candidate.get("source_pilot_sha256") != hashlib.sha256(source).hexdigest():
        raise CandidateGateError("SOURCE_PILOT_DRIFT")
    if candidate.get("decision_scope") != "user_approved_revision_direction_only":
        raise CandidateGateError("unrecognized human decision scope")
    if candidate.get("gold_release_approved") is not False:
        raise CandidateGateError("candidate cannot assert gold approval")
    try:
        results = validate_pilot(candidate, chunks)
        origin = json.loads(source)
    except (AnnotationError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateGateError("invalid candidate or source pilot") from exc
    if not isinstance(origin, dict) or not isinstance(origin.get("cases"), list):
        raise CandidateGateError("invalid source pilot")
    original_ids = {c["case_id"] for c in origin["cases"]}
    candidate_cases = candidate["cases"]
    if {c["case_id"] for c in candidate_cases} != original_ids:
        raise CandidateGateError("case identity drift")
    for case in candidate_cases:
        if case["annotation_status"] != "draft_pending_human_review":
            raise CandidateGateError("candidate must remain pending editorial approval")
    nine = next(c for c in candidate_cases if c["case_id"] == "EVAL-PILOT-009")
    if nine["case_type"] != "cross_doc" or len(nine["gold_evidence"]) < 2:
        raise CandidateGateError("cross-document replacement incomplete")
    ten = next(c for c in candidate_cases if c["case_id"] == "EVAL-PILOT-010")
    if ten["gold_evidence"] or ten["case_type"] != "insufficient_evidence":
        raise CandidateGateError("negative candidate must not acquire fabricated evidence")
    return results


def validate_candidate_files(
    candidate_path: Path, source_path: Path, chunks_path: Path
) -> dict[str, int]:
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        source = source_path.read_bytes()
        chunks = chunks_path.read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateGateError("unable to load candidate validation inputs") from exc
    if not isinstance(candidate, dict):
        raise CandidateGateError("candidate must be a JSON object")
    return validate_candidate(candidate, source, chunks)
