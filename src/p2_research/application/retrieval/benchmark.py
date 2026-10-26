"""Evaluate BM25 against signed pilot evidence at the chunk level."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from p2_research.application.retrieval.bm25 import (
    EXPECTED_CHUNKS_SHA256,
    Hit,
    RetrievalError,
)

GOLD_SHA256 = "7975fb03318da9cd573ac2f681ce1e0af92376ac449b4aa989ef58d3f089f8ce"


class SearchIndex(Protocol):
    """Shared BM25, Dense and Hybrid search contract."""

    def search(self, question: str, k: int = 5) -> list[Hit]: ...


def benchmark(
    index: SearchIndex, gold_bytes: bytes, ks: tuple[int, ...] = (1, 3, 5, 10)
) -> dict[str, Any]:
    if hashlib.sha256(gold_bytes).hexdigest() != GOLD_SHA256:
        raise RetrievalError("GOLD_DATASET_DRIFT")
    gold = json.loads(gold_bytes)
    if gold["corpus"]["chunks_sha256"] != EXPECTED_CHUNKS_SHA256:
        raise RetrievalError("GOLD_CORPUS_MISMATCH")
    if len(gold["cases"]) != 10 or tuple(sorted(ks)) != ks or min(ks) < 1 or max(ks) > 100:
        raise RetrievalError("invalid benchmark settings")
    answerable = [case for case in gold["cases"] if case["answerability"] == "answerable"]
    unanswerable = [
        case["case_id"] for case in gold["cases"] if case["answerability"] != "answerable"
    ]
    results: list[dict[str, Any]] = []
    for case in answerable:
        expected = {e["chunk_id"] for e in case["gold_evidence"]}
        if not expected:
            raise RetrievalError("answerable case without Gold evidence")
        hits = index.search(case["question"], max(ks))
        observed = [hit.chunk_id for hit in hits]
        ranks = [observed.index(cid) + 1 for cid in expected if cid in observed]
        first = min(ranks) if ranks else None
        results.append(
            {
                "case_id": case["case_id"],
                "language": case["language"],
                "gold_chunk_count": len(expected),
                "first_relevant_rank": first,
                "retrieved_chunk_ids": observed,
                "recall": {
                    str(k): round(len(expected.intersection(observed[:k])) / len(expected), 6)
                    for k in ks
                },
                "reciprocal_rank": round(1 / first, 6) if first is not None else 0.0,
            }
        )
    n = len(results)
    return {
        "schema_version": 1,
        "retriever": "bm25-lexical-v0.1",
        "corpus_sha256": EXPECTED_CHUNKS_SHA256,
        "gold_sha256": GOLD_SHA256,
        "answerable_cases": n,
        "unanswerable_cases_excluded": unanswerable,
        "recall_at_k": {
            str(k): round(sum(r["recall"][str(k)] for r in results) / n, 6) for k in ks
        },
        "mrr_at_max_k": round(sum(r["reciprocal_rank"] for r in results) / n, 6),
        "cases": results,
        "notes": "Chunk-level Gold Recall@K; unanswerable cases require answer/abstention evaluation, not retrieval Recall.",
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    if path.is_symlink():
        raise RetrievalError("refusing output symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
