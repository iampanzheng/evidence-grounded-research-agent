"""Reproducible locked-corpus bootstrap and independent offline verification."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .acquisition import acquire_document
from .chunking import MarkdownChunk, chunk_cached_snapshot, chunks_manifest
from .provenance import DocumentProvenance, audit_snapshot, provenance_manifest
from .source_contracts import LockedSnapshot, load_source_contracts


class BootstrapError(ValueError):
    """A corpus bootstrap or reproducibility invariant failed."""


@dataclass(frozen=True)
class BootstrapArtifacts:
    provenance: bytes
    chunks: bytes
    run: bytes
    document_count: int
    chunk_count: int


def _canonical(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _selection(source_dir: Path, profile: str) -> tuple[LockedSnapshot, ...]:
    _, snapshots = load_source_contracts(source_dir)
    raw = json.loads((source_dir / "snapshots.lock.json").read_text(encoding="utf-8"))
    try:
        selection = raw["profiles"][profile]
        permitted = set(selection["languages"])
        ids = selection["snapshot_ids"]
    except (KeyError, TypeError) as exc:
        raise BootstrapError("invalid locked profile") from exc
    if not isinstance(ids, list) or not ids or not isinstance(permitted, set):
        raise BootstrapError("empty locked profile")
    selected: list[LockedSnapshot] = []
    for sid in ids:
        if sid not in snapshots:
            raise BootstrapError("profile references unknown snapshot")
        snap = snapshots[sid]
        docs = tuple(doc for doc in snap.documents if doc.language in permitted)
        if not docs:
            raise BootstrapError("profile selected no documents")
        selected.append(replace(snap, documents=docs))
    return tuple(selected)


def build_corpus(
    source_dir: Path,
    cache_dir: Path,
    *,
    profile: str = "research",
    offline: bool = True,
    max_chunk_bytes: int = 2048,
) -> BootstrapArtifacts:
    """Acquire pinned bytes, audit receipts, chunk, and construct canonical artifacts."""
    if max_chunk_bytes <= 0:
        raise BootstrapError("invalid chunk size")
    snaps = _selection(source_dir, profile)
    provenances: list[DocumentProvenance] = []
    chunks: list[MarkdownChunk] = []
    for snapshot in snaps:
        for doc in snapshot.documents:
            acquire_document(snapshot, doc, cache_dir, offline=offline)
        provenances.extend(audit_snapshot(snapshot, cache_dir))
        chunks.extend(chunk_cached_snapshot(snapshot, cache_dir, max_chunk_bytes=max_chunk_bytes))
    provenance = provenance_manifest(tuple(provenances))
    chunk_bytes = chunks_manifest(tuple(chunks))
    lock_hash = _sha((source_dir / "snapshots.lock.json").read_bytes())
    run = _canonical(
        {
            "schema_version": 1,
            "profile": profile,
            "chunking_algorithm": "s14-v0.2-byte-exact",
            "max_chunk_bytes": max_chunk_bytes,
            "snapshot_lock_sha256": lock_hash,
            "document_count": len(provenances),
            "chunk_count": len(chunks),
            "provenance_sha256": _sha(provenance),
            "chunks_sha256": _sha(chunk_bytes),
            "public_distribution_approved": False,
        }
    )
    return BootstrapArtifacts(provenance, chunk_bytes, run, len(provenances), len(chunks))


def write_artifact(path: Path, content: bytes) -> None:
    """Write after *all* verification succeeds; avoid symlink destination and partial files."""
    if path.is_symlink() or path.parent.is_symlink():
        raise BootstrapError("refusing artifact symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".p2-", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise BootstrapError("refusing artifact symlink")
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def verify_artifacts(
    artifacts: BootstrapArtifacts,
    *,
    expected_chunks_sha256: str | None = None,
    saved_run: bytes | None = None,
) -> None:
    """Verify deterministic inputs against a saved run and/or a reference digest."""
    if expected_chunks_sha256 is not None and _sha(artifacts.chunks) != expected_chunks_sha256:
        raise BootstrapError("CHUNK_MANIFEST_DRIFT: rebuilt chunks differ from reference")
    if saved_run is not None and saved_run != artifacts.run:
        raise BootstrapError("RUN_MANIFEST_DRIFT: cached run metadata differs from rebuild")
