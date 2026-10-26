"""Inspect byte-exact frozen markdown for attribution flags without granting rights."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .license_preflight import LicensePreflightError, build_preflight

EXPECTED_CHUNKS_SHA256 = "a312e11036c2a7d6b723c9fbc5dfd8d984ec9865be7e41b00fdb9feaaa4d7fa1"

# Indicators trigger human inspection; a zero count is NOT proof of clearance.
INDICATORS: dict[str, str] = {
    "copyright_or_license": r"(?i)copyright|©|all rights reserved|SPDX-License-Identifier|licensed under|版权|版权所有|许可证|许可协议",
    "markdown_images": r"!\[[^\]\n]*\]\([^\n)]*\)",
    "html_images": r"(?i)<img\b[^>]*>",
    "html_embeds": r"(?i)<(?:iframe|video|audio|embed|object)\b[^>]*>",
    "external_link": r"https?://[^\s)<>\]\"']+",
    "attribution_language": r"(?i)reprinted from|adapted from|source:|来源[:：]|转载|原文链接|作者[:：]",
}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _reassemble(items: list[dict[str, Any]], *, path: str, length: int) -> bytes:
    cursor = 0
    assembled = bytearray()
    for chunk in sorted(items, key=lambda item: item["start_byte"]):
        if chunk.get("source_path") != path or chunk.get("start_byte") != cursor:
            raise LicensePreflightError(f"CHUNK_COVERAGE_GAP: {path}")
        data = chunk["text"].encode("utf-8")
        if chunk.get("end_byte") != cursor + len(data):
            raise LicensePreflightError(f"CHUNK_BYTE_RANGE_DRIFT: {path}")
        assembled.extend(data)
        cursor += len(data)
    if cursor != length:
        raise LicensePreflightError(f"CHUNK_COVERAGE_INCOMPLETE: {path}")
    return bytes(assembled)


def inspect_frozen_documents(root: Path, manifest: Path) -> dict[str, Any]:
    """Scan all 24 frozen documents reconstructed from *complete* chunk coverage."""
    preflight = build_preflight(root)
    raw_manifest = manifest.read_bytes()
    if _sha(raw_manifest) != EXPECTED_CHUNKS_SHA256:
        raise LicensePreflightError("CHUNK_MANIFEST_DRIFT")
    parsed = json.loads(raw_manifest)
    chunks = parsed.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 409:
        raise LicensePreflightError("EXPECTED_409_CHUNKS")
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        by_path[chunk["source_path"]].append(chunk)
    lock = json.loads((root / "data_sources/snapshots.lock.json").read_text(encoding="utf-8"))
    documents = {item["path"]: item for item in lock["snapshots"][0]["documents"]}
    if set(by_path) != set(documents):
        raise LicensePreflightError("DOCUMENT_SET_DRIFT")
    rows: list[dict[str, Any]] = []
    for pre in preflight["files"]:
        path = pre["path"]
        original = documents[path]
        source = _reassemble(by_path[path], path=path, length=original["bytes"])
        if _sha(source) != original["sha256"]:
            raise LicensePreflightError(f"SOURCE_BYTES_DRIFT: {path}")
        text = source.decode("utf-8")
        findings: dict[str, Any] = {}
        for key, pattern in INDICATORS.items():
            matches = list(re.finditer(pattern, text))
            findings[key] = {
                "count": len(matches),
                "line_numbers": [text.count("\n", 0, match.start()) + 1 for match in matches[:20]],
                "truncated": len(matches) > 20,
            }
        rows.append(
            {
                "path": path,
                "document_sha256": _sha(source),
                "byte_count": len(source),
                "chunk_count": len(by_path[path]),
                "source_url": pre["source_url"],
                "indicators": findings,
                "inspection_status": "byte_complete_automated_screening",
                "third_party_content_clearance": "not_established",
                "recommended_action": "human_review_required",
                "public_distribution_decision": "pending",
            }
        )
    return {
        "schema_version": 1,
        "snapshot_id": preflight["snapshot_id"],
        "commit_sha": preflight["commit_sha"],
        "chunks_sha256": EXPECTED_CHUNKS_SHA256,
        "file_count": len(rows),
        "reviewed_for_indicators": len(rows),
        "human_approved": 0,
        "public_distribution_approved": False,
        "method": "reconstruct_exact_source_bytes_from_complete_frozen_chunks_and_scan_indicators",
        "limitations": [
            "Indicators are not a legal determination or proof of content ownership.",
            "Referenced external media are not fetched; linked content may require separate rights.",
            "Repository LICENSE and applicable NOTICE obligations still require review.",
            "No automatic approval or mutation of license-reviews.json is performed.",
        ],
        "files": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen locked documents for license indicators")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = (
        json.dumps(inspect_frozen_documents(args.root, args.chunks), indent=2, ensure_ascii=False)
        + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
