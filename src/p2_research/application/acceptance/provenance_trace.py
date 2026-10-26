"""Bidirectional source/chunk/Gold trace for an immutable private corpus.

Reports metadata only; source text is never serialized into the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .license_file_review import EXPECTED_CHUNKS_SHA256, _reassemble
from .stage1 import GOLD_SHA256


class ProvenanceTraceError(ValueError):
    """Frozen provenance inputs fail integrity or referential checks."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ProvenanceTraceError(f"Cannot parse {path.name}") from exc
    if not isinstance(value, dict):
        raise ProvenanceTraceError("Expected JSON object")
    return value


def trace_corpus(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Verify all source coverage and both forward/reverse Gold citations."""
    try:
        chunk_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise ProvenanceTraceError("Missing private chunk manifest") from exc
    if hashlib.sha256(chunk_bytes).hexdigest() != EXPECTED_CHUNKS_SHA256:
        raise ProvenanceTraceError("CHUNK_MANIFEST_DRIFT")
    gold_path = root / "evaluation/datasets/pilot-gold-v1.json"
    if hashlib.sha256(gold_path.read_bytes()).hexdigest() != GOLD_SHA256:
        raise ProvenanceTraceError("GOLD_DATASET_DRIFT")
    manifest = _load(manifest_path)
    gold = _load(gold_path)
    lock = _load(root / "data_sources/snapshots.lock.json")
    docs = lock["snapshots"][0]["documents"]
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 409 or len(docs) != 24:
        raise ProvenanceTraceError("CORPUS_CARDINALITY_DRIFT")
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_chunk: dict[str, dict[str, Any]] = {}
    for item in chunks:
        key = item["chunk_id"]
        if key in by_chunk:
            raise ProvenanceTraceError("DUPLICATE_CHUNK")
        by_chunk[key] = item
        by_path[item["source_path"]].append(item)
    if set(by_path) != {d["path"] for d in docs}:
        raise ProvenanceTraceError("SOURCE_SET_DRIFT")
    reverse: dict[str, list[str]] = defaultdict(list)
    total_refs = 0
    for case in gold["cases"]:
        for evidence in case["gold_evidence"]:
            chunk = by_chunk.get(evidence["chunk_id"])
            if chunk is None or any(
                evidence[k] != chunk[k] for k in ("document_id", "source_path", "source_sha256")
            ):
                raise ProvenanceTraceError("GOLD_SOURCE_LINK_DRIFT")
            if (
                not chunk["start_byte"]
                <= evidence["start_byte"]
                < evidence["end_byte"]
                <= chunk["end_byte"]
            ):
                raise ProvenanceTraceError("GOLD_RANGE_DRIFT")
            quoted = chunk["text"].encode("utf-8")[
                evidence["start_byte"] - chunk["start_byte"] : evidence["end_byte"]
                - chunk["start_byte"]
            ]
            if hashlib.sha256(quoted).hexdigest() != evidence["quote_sha256"]:
                raise ProvenanceTraceError("GOLD_QUOTE_DRIFT")
            reverse[evidence["chunk_id"]].append(case["case_id"])
            total_refs += 1
    files = []
    for doc in sorted(docs, key=lambda d: d["path"]):
        path = doc["path"]
        entries = sorted(by_path[path], key=lambda c: c["start_byte"])
        source = _reassemble(entries, path=path, length=doc["bytes"])
        if hashlib.sha256(source).hexdigest() != doc["sha256"]:
            raise ProvenanceTraceError("SOURCE_HASH_DRIFT")
        for chunk in entries:
            if chunk["source_sha256"] != doc["sha256"]:
                raise ProvenanceTraceError("CHUNK_SOURCE_HASH_DRIFT")
        cases = sorted({cid for chunk in entries for cid in reverse.get(chunk["chunk_id"], [])})
        files.append(
            {
                "source_path": path,
                "source_sha256": doc["sha256"],
                "source_url": entries[0]["source_url"],
                "chunk_count": len(entries),
                "chunk_ids": [chunk["chunk_id"] for chunk in entries],
                "gold_case_ids": cases,
            }
        )
    return {
        "schema_version": 1,
        "chunks_sha256": EXPECTED_CHUNKS_SHA256,
        "gold_sha256": GOLD_SHA256,
        "document_count": len(files),
        "chunk_count": len(chunks),
        "gold_case_count": len(gold["cases"]),
        "evidence_links": total_refs,
        "forward_reverse_trace_verified": True,
        "includes_source_text": False,
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate byte-exact bidirectional provenance")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(trace_corpus(args.root, args.chunks), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
