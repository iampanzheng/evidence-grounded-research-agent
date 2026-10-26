"""Strict, read-only pilot annotation checks against a frozen chunk manifest."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


class AnnotationError(ValueError):
    """Pilot record or provenance is not independently verifiable."""


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnnotationError("expected JSON object")
    return value


def validate_pilot(pilot: dict[str, Any], manifest_bytes: bytes) -> dict[str, int]:
    """Verify schema, frozen manifest, citations, quoted bytes, and review state.

    Successful validation means evidence integrity only, not editorial approval.
    """
    if pilot.get("schema_version") != 1:
        raise AnnotationError("unsupported pilot schema")
    corpus = _object(pilot.get("corpus"))
    if (
        corpus.get("profile") != "research"
        or corpus.get("chunking_algorithm") != "s14-v0.2-byte-exact"
    ):
        raise AnnotationError("unexpected corpus baseline")
    if corpus.get("max_chunk_bytes") != 2048:
        raise AnnotationError("chunking budget drift")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if corpus.get("chunks_sha256") != digest:
        raise AnnotationError("CHUNK_MANIFEST_DRIFT")
    try:
        manifest = _object(json.loads(manifest_bytes))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationError("malformed chunk manifest") from exc
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("chunks"), list):
        raise AnnotationError("invalid chunk manifest")
    chunks: dict[str, dict[str, Any]] = {}
    for value in manifest["chunks"]:
        chunk = _object(value)
        key = chunk.get("chunk_id")
        if not isinstance(key, str) or key in chunks:
            raise AnnotationError("duplicate/invalid chunk id")
        chunks[key] = chunk
    cases = pilot.get("cases")
    if not isinstance(cases, list) or len(cases) != 10:
        raise AnnotationError("pilot must contain exactly ten cases")
    seen: set[str] = set()
    evidence_count = 0
    for record in cases:
        case = _object(record)
        ident = case.get("case_id")
        if (
            not isinstance(ident, str)
            or not re.fullmatch(r"EVAL-PILOT-\d{3}", ident)
            or ident in seen
        ):
            raise AnnotationError("invalid/duplicate case_id")
        seen.add(ident)
        if case.get("language") not in {"en", "zh"}:
            raise AnnotationError("unsupported language")
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise AnnotationError("question missing")
        if (
            not isinstance(case.get("expected_answer_notes"), str)
            or not case["expected_answer_notes"].strip()
        ):
            raise AnnotationError("expected answer notes missing")
        if case.get("case_type") not in {"single_doc", "cross_doc", "insufficient_evidence"}:
            raise AnnotationError("invalid case type")
        status = case.get("annotation_status")
        if status not in {"draft_pending_human_review", "approved", "rejected"}:
            raise AnnotationError("invalid review status")
        if status == "draft_pending_human_review":
            if case.get("reviewer") is not None or case.get("reviewed_at") is not None:
                raise AnnotationError("unreviewed case must have no reviewer or timestamp")
        elif not isinstance(case.get("reviewer"), str) or not isinstance(
            case.get("reviewed_at"), str
        ):
            raise AnnotationError("review decision lacks audit metadata")
        refs = case.get("gold_evidence")
        if not isinstance(refs, list):
            raise AnnotationError("gold_evidence must be a list")
        answerable = case.get("answerability")
        if answerable not in {"answerable", "unanswerable"}:
            raise AnnotationError("invalid answerability")
        if (answerable == "answerable" and not refs) or (answerable == "unanswerable" and refs):
            raise AnnotationError("evidence/answerability mismatch")
        if case.get("case_type") == "insufficient_evidence" and answerable != "unanswerable":
            raise AnnotationError("insufficient evidence case must be unanswerable")
        referenced_documents: set[str] = set()
        for ref_value in refs:
            ref = _object(ref_value)
            ref_id = ref.get("chunk_id")
            if not isinstance(ref_id, str):
                raise AnnotationError("invalid gold chunk id")
            chunk_match = chunks.get(ref_id)
            if chunk_match is None:
                raise AnnotationError("gold evidence references missing chunk")
            chunk = chunk_match
            if any(
                ref.get(field) != chunk.get(field)
                for field in ("document_id", "source_path", "source_sha256")
            ):
                raise AnnotationError("gold evidence source provenance mismatch")
            if chunk.get("language") != case.get("language"):
                raise AnnotationError("gold evidence language mismatch")
            start, end = ref.get("start_byte"), ref.get("end_byte")
            chunk_start, chunk_end = chunk.get("start_byte"), chunk.get("end_byte")
            if not all(type(x) is int for x in (start, end, chunk_start, chunk_end)):
                raise AnnotationError("byte ranges must be integers")
            assert isinstance(start, int) and isinstance(end, int)
            assert isinstance(chunk_start, int) and isinstance(chunk_end, int)
            if not chunk_start <= start < end <= chunk_end:
                raise AnnotationError("gold evidence outside chunk")
            text = chunk.get("text")
            if not isinstance(text, str):
                raise AnnotationError("chunk text missing")
            quote = text.encode("utf-8")[start - chunk_start : end - chunk_start]
            try:
                quote.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AnnotationError("evidence boundary splits UTF-8") from exc
            if hashlib.sha256(quote).hexdigest() != ref.get("quote_sha256"):
                raise AnnotationError("gold quote hash mismatch")
            referenced_documents.add(chunk["document_id"])
            evidence_count += 1
        if case.get("case_type") == "cross_doc" and len(referenced_documents) < 2:
            raise AnnotationError("cross document case requires at least two documents")
    return {
        "cases": len(cases),
        "evidence_spans": evidence_count,
        "pending_review": sum(
            c["annotation_status"] == "draft_pending_human_review" for c in cases
        ),
    }


def validate_pilot_files(pilot_path: Path, manifest_path: Path) -> dict[str, int]:
    try:
        pilot = _object(json.loads(pilot_path.read_text(encoding="utf-8")))
        chunks = manifest_path.read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationError(f"unable to read evaluation inputs: {type(exc).__name__}") from exc
    return validate_pilot(pilot, chunks)
