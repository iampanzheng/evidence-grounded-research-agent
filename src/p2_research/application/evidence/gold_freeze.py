"""Verify an explicitly signed, content-addressed pilot Gold dataset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .annotations import AnnotationError, validate_pilot


class GoldFreezeError(ValueError):
    """Gold dataset and its human decision record do not match."""


def validate_gold(
    candidate_bytes: bytes, signoff_bytes: bytes, gold_bytes: bytes, chunks_bytes: bytes
) -> dict[str, int]:
    """Fail closed on version drift, partial reviews, or altered Gold content."""
    try:
        candidate = json.loads(candidate_bytes)
        signoff = json.loads(signoff_bytes)
        gold = json.loads(gold_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GoldFreezeError("malformed freeze input") from exc
    if not all(isinstance(x, dict) for x in (candidate, signoff, gold)):
        raise GoldFreezeError("freeze inputs must be objects")
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    signed_sha = hashlib.sha256(signoff_bytes).hexdigest()
    if signoff.get("source_candidate_sha256") != candidate_sha:
        raise GoldFreezeError("CANDIDATE_DRIFT")
    if gold.get("signoff_sha256") != signed_sha:
        raise GoldFreezeError("SIGNOFF_DRIFT")
    if signoff.get("review_scope") != "final_question_answer_evidence_signoff":
        raise GoldFreezeError("wrong review scope")
    if signoff.get("human_signoff_complete") is not True:
        raise GoldFreezeError("human signoff incomplete")
    if signoff.get("decision_source") != "explicit_user_chat_signoff_2026-10-12":
        raise GoldFreezeError("review provenance mismatch")
    if gold.get("decision_scope") != "final_human_content_signoff_recorded":
        raise GoldFreezeError("no final Gold signoff")
    if gold.get("gold_release_approved") is not True:
        raise GoldFreezeError("Gold release gate closed")
    signed = signoff.get("cases")
    pending = candidate.get("cases")
    accepted = gold.get("cases")
    if not all(isinstance(x, list) and len(x) == 10 for x in (signed, pending, accepted)):
        raise GoldFreezeError("incomplete cases")
    decisions: dict[str, dict[str, Any]] = {}
    for entry in signed:
        if not isinstance(entry, dict):
            raise GoldFreezeError("invalid decision")
        ident = entry.get("case_id")
        if not isinstance(ident, str) or ident in decisions:
            raise GoldFreezeError("duplicate decision")
        if (
            entry.get("human_decision") != "approve"
            or entry.get("review_source") != signoff["decision_source"]
            or entry.get("human_reviewed_on") != signoff.get("reviewed_on")
        ):
            raise GoldFreezeError("unsigned or non-approved decision")
        decisions[ident] = entry
    if len(decisions) != 10:
        raise GoldFreezeError("missing human decisions")
    expected = {x["case_id"]: x for x in pending}
    if set(expected) != set(decisions):
        raise GoldFreezeError("reviewed case identities drifted")
    for entry in accepted:
        if not isinstance(entry, dict):
            raise GoldFreezeError("invalid gold case")
        ident = entry.get("case_id")
        original = expected.get(ident)
        if original is None:
            raise GoldFreezeError("unexpected gold case")
        control = dict(entry)
        for field in ("annotation_status", "reviewer", "reviewed_at"):
            control.pop(field, None)
        original_control = dict(original)
        for field in ("annotation_status", "reviewer", "reviewed_at"):
            original_control.pop(field, None)
        if control != original_control:
            raise GoldFreezeError("GOLD_CONTENT_DRIFT")
        if (
            entry.get("annotation_status") != "approved"
            or entry.get("reviewer") != "user-confirmed-chat"
            or entry.get("reviewed_at") != signoff.get("reviewed_on")
        ):
            raise GoldFreezeError("invalid Gold approval metadata")
    if len({x["case_id"] for x in accepted}) != 10:
        raise GoldFreezeError("duplicate Gold case")
    try:
        outcome = validate_pilot(gold, chunks_bytes)
    except AnnotationError as exc:
        raise GoldFreezeError(f"evidence integrity failed: {exc}") from exc
    if outcome["pending_review"]:
        raise GoldFreezeError("pending Gold case")
    return {"approved": 10, "evidence_spans": outcome["evidence_spans"]}


def validate_gold_files(
    candidate_path: Path, signoff_path: Path, gold_path: Path, chunks_path: Path
) -> dict[str, int]:
    try:
        return validate_gold(
            candidate_path.read_bytes(),
            signoff_path.read_bytes(),
            gold_path.read_bytes(),
            chunks_path.read_bytes(),
        )
    except OSError as exc:
        raise GoldFreezeError("missing Gold validation input") from exc
