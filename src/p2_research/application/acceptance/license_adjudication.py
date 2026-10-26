"""Prepare human-facing license adjudication evidence; never grant distribution rights."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .license_file_review import EXPECTED_CHUNKS_SHA256, _reassemble
from .license_preflight import LicensePreflightError, build_preflight

IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(([^\n)]+)\)|<img\b[^>]*\bsrc=[\"']([^\"']+)", re.I)
RIGHTS_RE = re.compile(r"(?i)版权|copyright|署名|禁止演绎|licensed under|作者[:：]|©")


def assemble_review(root: Path, manifest: Path) -> dict[str, Any]:
    """Find rights conflicts and media targets in byte-verified 24-document corpus."""
    pre = build_preflight(root)
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_CHUNKS_SHA256:
        raise LicensePreflightError("CHUNK_MANIFEST_DRIFT")
    parsed = json.loads(raw)
    chunks = parsed.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 409:
        raise LicensePreflightError("EXPECTED_409_CHUNKS")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in chunks:
        groups[c["source_path"]].append(c)
    locked = json.loads((root / "data_sources/snapshots.lock.json").read_text())
    docs = {d["path"]: d for d in locked["snapshots"][0]["documents"]}
    if set(groups) != set(docs):
        raise LicensePreflightError("DOCUMENT_SET_DRIFT")
    items: list[dict[str, Any]] = []
    for file in pre["files"]:
        path = file["path"]
        doc = docs[path]
        data = _reassemble(groups[path], path=path, length=doc["bytes"])
        if hashlib.sha256(data).hexdigest() != doc["sha256"]:
            raise LicensePreflightError(f"SOURCE_BYTES_DRIFT: {path}")
        body = data.decode("utf-8")
        lines = body.splitlines()
        media: list[dict[str, Any]] = []
        rights_lines: list[int] = []
        for i, line in enumerate(lines, 1):
            if RIGHTS_RE.search(line):
                rights_lines.append(i)
            for match in IMAGE_RE.finditer(line):
                target = (match.group(1) or match.group(2)).strip().split()[0]
                media.append(
                    {
                        "line": i,
                        "target": target,
                        "target_class": "remote"
                        if target.startswith(("https://", "http://"))
                        else "site_relative"
                        if target.startswith("/")
                        else "relative",
                        "asset_rights_verified": False,
                    }
                )
        ambiguous = bool(re.search("署名.{0,12}禁止演绎", body))
        recommendation = (
            "escalate_rights_conflict"
            if ambiguous
            else ("verify_media_provenance" if media else "verify_text_rights_and_attribution")
        )
        items.append(
            {
                "path": path,
                "document_sha256": doc["sha256"],
                "source_url": file["source_url"],
                "embedded_media": media,
                "media_reference_count": len(media),
                "rights_indicator_lines": rights_lines[:30],
                "ambiguous_no_derivatives_wording": ambiguous,
                "recommendation": recommendation,
                "text_distribution_decision": "pending",
                "embedded_media_distribution_decision": "pending" if media else "not_applicable",
                "public_distribution_approved": False,
                "human_review_required": True,
            }
        )
    media_targets = sorted({m["target"] for f in items for m in f["embedded_media"]})
    return {
        "schema_version": 1,
        "snapshot_id": pre["snapshot_id"],
        "commit_sha": pre["commit_sha"],
        "chunks_sha256": EXPECTED_CHUNKS_SHA256,
        "document_count": len(items),
        "public_distribution_approved": False,
        "human_approved": 0,
        "ambiguous_rights_files": sum(bool(x["ambiguous_no_derivatives_wording"]) for x in items),
        "files_with_embedded_media": sum(bool(x["embedded_media"]) for x in items),
        "media_references": sum(len(x["embedded_media"]) for x in items),
        "distinct_media_targets": media_targets,
        "scope": "metadata-only human review support; image assets not retrieved or licensed",
        "files": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare locked-corpus rights adjudication evidence"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = (
        json.dumps(assemble_review(args.root, args.chunks), ensure_ascii=False, indent=2) + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
