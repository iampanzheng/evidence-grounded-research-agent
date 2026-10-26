"""Deterministic provenance and distribution audit for pinned corpus snapshots.

This checks local evidence, not upstream repository authenticity or legal licensing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .acquisition import AcquisitionError, acquire_document
from .source_contracts import LockedDocument, LockedSnapshot

ReviewState = Literal["pending_file_level_review", "approved", "rejected"]


@dataclass(frozen=True, slots=True)
class DocumentProvenance:
    snapshot_id: str
    source_id: str
    repository: str
    commit_sha: str
    source_path: str
    source_url: str
    sha256: str
    git_blob_sha: str
    size_bytes: int
    language: str
    upstream_verification: str
    license_review: ReviewState
    public_distribution_approved: bool


class ProvenanceError(ValueError):
    """Invalid provenance or distribution policy."""


def _review_state(value: str) -> ReviewState:
    if value not in {"pending_file_level_review", "approved", "rejected"}:
        raise ProvenanceError("invalid license review")
    return value  # type: ignore[return-value]


def verify_document_provenance(
    snapshot: LockedSnapshot, doc: LockedDocument, cache_root: Path
) -> DocumentProvenance:
    """Verify cache contents/receipt against locked source, even when offline."""
    try:
        cached = acquire_document(snapshot, doc, cache_root, offline=True)
    except AcquisitionError as exc:
        raise ProvenanceError(f"unverifiable locked document: {exc}") from exc
    return DocumentProvenance(
        snapshot_id=snapshot.snapshot_id,
        source_id=snapshot.source_id,
        repository=snapshot.repository,
        commit_sha=snapshot.commit_sha,
        source_path=doc.path,
        source_url=doc.source_url,
        sha256=doc.sha256,
        git_blob_sha=cached.git_blob_sha,
        size_bytes=doc.size_bytes,
        language=doc.language,
        upstream_verification=snapshot.upstream_verification,
        license_review=_review_state(doc.license_review),
        public_distribution_approved=doc.public_distribution_approved,
    )


def audit_snapshot(snapshot: LockedSnapshot, cache_root: Path) -> tuple[DocumentProvenance, ...]:
    """Fail the audit rather than silently omitting a missing or corrupt file."""
    return tuple(
        verify_document_provenance(snapshot, doc, cache_root) for doc in snapshot.documents
    )


def require_public_distribution_allowed(item: DocumentProvenance) -> None:
    """Fail closed for unreviewed licenses and independently unverified sources."""
    if (
        item.license_review != "approved"
        or not item.public_distribution_approved
        or item.upstream_verification != "verified"
    ):
        raise ProvenanceError(f"PUBLIC_DISTRIBUTION_BLOCKED: {item.source_path}")


def provenance_manifest(items: tuple[DocumentProvenance, ...]) -> bytes:
    """Canonical reproducible JSON; independent of machine-local cache paths."""
    data = {"schema_version": 1, "documents": [asdict(item) for item in items]}
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def manifest_sha256(items: tuple[DocumentProvenance, ...]) -> str:
    return hashlib.sha256(provenance_manifest(items)).hexdigest()
