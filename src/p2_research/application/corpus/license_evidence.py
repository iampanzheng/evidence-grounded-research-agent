"""Deterministic license-review evidence inventory; never grants distribution rights."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .acquisition import AcquisitionError, acquire_document
from .source_contracts import LockedSnapshot
from .source_review import FileReview, SourceReviewError

_SIGNAL = re.compile(
    r"copyright|SPDX-License-Identifier|creative commons|all rights reserved|"
    r"licensed under|third.party|attribution|\bNOTICE\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FileEvidence:
    path: str
    sha256: str
    review_decision: str
    cache_status: str
    signals: tuple[str, ...]
    requires_review: bool


def collect_license_evidence(
    snapshot: LockedSnapshot,
    reviews: tuple[FileReview, ...],
    cache_root: Path,
) -> tuple[FileEvidence, ...]:
    """Scan verified *cached* documents without network or approval side effects.

    A text signal is a triage hint; absence of signals is not proof of ownership.
    """
    if len(reviews) != len(snapshot.documents):
        raise SourceReviewError("file review count does not match snapshot")
    by_path = {item.path: item for item in reviews}
    if len(by_path) != len(reviews):
        raise SourceReviewError("duplicate review records")
    results: list[FileEvidence] = []
    for doc in sorted(snapshot.documents, key=lambda value: value.path):
        review = by_path.get(doc.path)
        if review is None or review.document_sha256 != doc.sha256:
            raise SourceReviewError("review identity does not match locked document")
        try:
            acquired = acquire_document(snapshot, doc, cache_root, offline=True)
        except AcquisitionError as exc:
            # Missing data must stay visible; corrupted cache must fail closed.
            if "OFFLINE_CACHE_MISS" not in str(exc):
                raise SourceReviewError(f"cached evidence invalid: {doc.path}") from exc
            status = "missing"
            signals: tuple[str, ...] = ()
        else:
            try:
                content = acquired.cached_path.read_bytes().decode("utf-8")
            except UnicodeError as exc:
                raise SourceReviewError(f"invalid UTF-8: {doc.path}") from exc
            signals = tuple(
                sorted(set(match.group(0).lower() for match in _SIGNAL.finditer(content)))
            )
            status = "verified_cache"
        results.append(FileEvidence(doc.path, doc.sha256, review.decision, status, signals, True))
    return tuple(results)


def license_evidence_report(items: tuple[FileEvidence, ...], snapshot: LockedSnapshot) -> bytes:
    """Canonical JSON contains no raw licensed document text or local absolute paths."""
    payload = {
        "schema_version": 1,
        "snapshot_id": snapshot.snapshot_id,
        "commit_sha": snapshot.commit_sha,
        "status": "REVIEW_REQUIRED",
        "files": [asdict(item) for item in items],
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def evidence_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
