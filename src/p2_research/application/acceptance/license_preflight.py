"""Fail-closed, offline preparation of per-file license review decisions.

This module does not convey rights or update the source review register.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


class LicensePreflightError(ValueError):
    """Invalid or drifted frozen license inventory."""


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LicensePreflightError(f"Cannot load {path}") from exc
    if not isinstance(value, dict):
        raise LicensePreflightError(f"Invalid JSON object: {path}")
    return value


def build_preflight(root: Path) -> dict[str, Any]:
    """Bind 24 pending files to the immutable snapshot without changing approval state."""
    lock = _read(root / "data_sources/snapshots.lock.json")
    review_path = root / "data_sources/license-reviews.json"
    reviews = _read(review_path)
    snapshots = lock.get("snapshots")
    if not isinstance(snapshots, list) or len(snapshots) != 1:
        raise LicensePreflightError("Expected exactly one snapshot")
    snapshot = snapshots[0]
    if not isinstance(snapshot, dict):
        raise LicensePreflightError("Invalid snapshot")
    docs = snapshot.get("documents")
    files = reviews.get("files")
    if (
        not isinstance(docs, list)
        or not isinstance(files, list)
        or len(docs) != 24
        or len(files) != 24
    ):
        raise LicensePreflightError("Expected 24 locked docs and 24 review entries")
    if any(reviews.get(k) != snapshot.get(k) for k in ("snapshot_id", "commit_sha")):
        raise LicensePreflightError("Review / snapshot identity mismatch")
    index: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("path"), str):
            raise LicensePreflightError("Invalid locked document")
        if doc["path"] in index:
            raise LicensePreflightError("Duplicate locked document")
        index[doc["path"]] = doc
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in sorted(files, key=lambda value: value["path"]):
        path = entry.get("path")
        doc = index.get(path) if isinstance(path, str) else None
        if doc is None or path in seen or doc.get("sha256") != entry.get("document_sha256"):
            raise LicensePreflightError("Document identity mismatch or duplicate review")
        seen.add(path)
        if entry.get("decision") != "pending":
            raise LicensePreflightError("Preflight expects pending decisions; do not auto-approve")
        rows.append(
            {
                "path": path,
                "document_sha256": entry["document_sha256"],
                "source_url": (
                    f"https://github.com/apache/dubbo-website/blob/{snapshot['commit_sha']}/{path}"
                ),
                "repository_license_candidate": "Apache-2.0",
                "repository_license_status": "verified_at_pinned_commit",
                "file_content_inspection": "pending_verified_raw_file_inspection",
                "embedded_media_and_external_attribution": "not_yet_checked",
                "notice_obligations": "review_applicable_notices_and_attributions",
                "recommended_decision": "needs_manual_review",
                "distribution_decision": "pending",
            }
        )
    if len(seen) != len(index):
        raise LicensePreflightError("Missing reviewed source file")
    return {
        "schema_version": 1,
        "snapshot_id": snapshot["snapshot_id"],
        "commit_sha": snapshot["commit_sha"],
        "review_register_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
        "repository_license_url": reviews.get("repository_license_evidence"),
        "repository_license_verified": "Apache-2.0",
        "root_notice_status": "not_found_at_pinned_root_url_not_proof_of_absence_of_notices",
        "public_distribution_approved": False,
        "source_text_included": False,
        "file_count": len(rows),
        "files": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Produce unsigned 24-file license triage")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(build_preflight(args.root), indent=2, ensure_ascii=False) + "\n"
    if args.output is None:
        print(report, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
