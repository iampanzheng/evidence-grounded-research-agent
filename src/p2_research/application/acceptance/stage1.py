"""Conservative Stage 1 acceptance audit; evidence gaps are never inferred PASS."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

GOLD_SHA256 = "7975fb03318da9cd573ac2f681ce1e0af92376ac449b4aa989ef58d3f089f8ce"
CHUNKS_SHA256 = "a312e11036c2a7d6b723c9fbc5dfd8d984ec9865be7e41b00fdb9feaaa4d7fa1"


class AcceptanceError(ValueError):
    """An expected Stage 1 review input is absent or inconsistent."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AcceptanceError(f"Cannot read audit input {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AcceptanceError(f"Expected JSON object: {path}")
    return data


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AcceptanceError(f"Missing frozen dataset: {path}") from exc


def assess_stage1(root: Path) -> dict[str, Any]:
    """Produce a conservative review, never fabricating online or legal approvals."""
    lock = _read_json(root / "data_sources/snapshots.lock.json")
    reviews = _read_json(root / "data_sources/license-reviews.json")
    gold_path = root / "evaluation/datasets/pilot-gold-v1.json"
    gold = _read_json(gold_path)
    if _sha256(gold_path) != GOLD_SHA256:
        raise AcceptanceError("Frozen pilot Gold Dataset digest mismatch")
    cases = gold.get("cases")
    if not isinstance(cases, list) or len(cases) != 10:
        raise AcceptanceError("Gold pilot must have 10 cases")
    if gold.get("corpus", {}).get("chunks_sha256") != CHUNKS_SHA256:
        raise AcceptanceError("Gold pilot corpus digest mismatch")
    snapshots = lock.get("snapshots")
    if not isinstance(snapshots, list) or len(snapshots) != 1:
        raise AcceptanceError("Expected one pinned real-corpus snapshot")
    snapshot = snapshots[0]
    docs = snapshot.get("documents", [])
    files = reviews.get("files")
    if not isinstance(docs, list) or len(docs) != 24:
        raise AcceptanceError("Expected 24 locked source documents")
    if not isinstance(files, list) or len(files) != 24:
        raise AcceptanceError("Expected 24 per-file license reviews")
    if reviews.get("commit_sha") != snapshot.get("commit_sha"):
        raise AcceptanceError("Review commit does not match source snapshot")
    if reviews.get("snapshot_id") != snapshot.get("snapshot_id"):
        raise AcceptanceError("Review snapshot does not match lock")
    counts: dict[str, int] = {}
    for review in files:
        status = review.get("decision", "missing")
        if not isinstance(status, str):
            raise AcceptanceError("Invalid file license review decision")
        counts[status] = counts.get(status, 0) + 1
    approved_distribution = all(f.get("decision") == "approved" for f in files)
    # Reports of prior runs are evidence references, not a substitute for new local execution.
    rows = [
        (
            "CORPUS-ACC-001",
            "NEEDS_INDEPENDENT_RUN",
            "Fresh online bootstrap with no manual upload not independently evidenced in this package",
        ),
        (
            "CORPUS-ACC-002",
            "SUPPORTED_PENDING_LOCAL_REPLAY",
            "S1.5 recorded deterministic offline 24/409 rebuild and digest",
        ),
        (
            "CORPUS-ACC-003",
            "SUPPORTED_PENDING_LOCAL_REPLAY",
            "Offline missing-cache rejection covered by contract tests",
        ),
        (
            "CORPUS-ACC-004",
            "SUPPORTED_PENDING_LOCAL_REPLAY",
            "Security and source-validation negative tests present",
        ),
        (
            "CORPUS-ACC-005",
            "SUPPORTED_PENDING_LOCAL_REPLAY",
            "Byte-exact chunk provenance and validated Gold evidence; verify reverse links",
        ),
        (
            "CORPUS-ACC-006",
            "SUPPORTED_PENDING_LOCAL_REPLAY",
            "Gold v1 digest and frozen corpus lock bind evaluation cases",
        ),
        (
            "CORPUS-ACC-007",
            "BLOCKED" if not approved_distribution else "REVIEW_REQUIRED",
            "24 file decisions must be reviewed; no corpus-text publication authorized by this report",
        ),
        (
            "CORPUS-ACC-008",
            "PARTIAL",
            "Offline macOS suite confirmed by user; fresh online smoke/remote CI evidence separate",
        ),
    ]
    criteria = [{"id": code, "status": state, "reason": reason} for code, state, reason in rows]
    return {
        "schema_version": 1,
        "stage": "1",
        "decision": "NOT_CLOSED",
        "snapshot_id": snapshot["snapshot_id"],
        "commit_sha": snapshot["commit_sha"],
        "document_count": len(docs),
        "pilot_approved_cases": len(cases),
        "gold_sha256": GOLD_SHA256,
        "chunks_sha256": CHUNKS_SHA256,
        "license_decisions": counts,
        "public_distribution_approved": False,  # explicit separate gate
        "criteria": criteria,
        "unresolved": [r["id"] for r in criteria if r["status"] not in {"PASS"}],
    }


def main() -> None:
    """Print the Stage 1 review without changing tracked project artifacts."""
    import argparse

    parser = argparse.ArgumentParser(description="Conservative Stage 1 acceptance matrix")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    print(json.dumps(assess_stage1(args.root), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
