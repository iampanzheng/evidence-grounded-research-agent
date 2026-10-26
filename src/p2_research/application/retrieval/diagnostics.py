"""Evidence-level and document-level diagnostic metrics for frozen BM25 runs."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

from p2_research.application.retrieval.benchmark import GOLD_SHA256, benchmark
from p2_research.application.retrieval.bm25 import (
    EXPECTED_CHUNKS_SHA256,
    BM25Index,
    RetrievalError,
)


def diagnose(index: BM25Index, gold_bytes: bytes, *, repetitions: int = 7) -> dict[str, Any]:
    """Compute macro recalls, all-evidence coverage and latency without publishing text."""
    if not 1 <= repetitions <= 100:
        raise RetrievalError("repetitions must be within [1, 100]")
    baseline = benchmark(index, gold_bytes)
    if hashlib.sha256(gold_bytes).hexdigest() != GOLD_SHA256:
        raise RetrievalError("GOLD_DATASET_DRIFT")
    cases = json.loads(gold_bytes)["cases"]
    docs = {chunk["chunk_id"]: chunk["document_id"] for chunk in index.chunks}
    answers: list[dict[str, Any]] = []
    durations: list[float] = []
    for case in cases:
        if case["answerability"] != "answerable":
            continue
        expected = {e["chunk_id"] for e in case["gold_evidence"]}
        if not expected or any(k not in docs for k in expected):
            raise RetrievalError("missing Gold chunk from index")
        hits = index.search(case["question"], 10)
        retrieved = [h.chunk_id for h in hits]
        gold_documents = {docs[k] for k in expected}
        row = next(r for r in baseline["cases"] if r["case_id"] == case["case_id"])
        for _ in range(repetitions):
            start = time.perf_counter_ns()
            index.search(case["question"], 10)
            durations.append((time.perf_counter_ns() - start) / 1_000_000)
        gold_ranks = {
            k: retrieved.index(k) + 1 if k in retrieved else None for k in sorted(expected)
        }
        document_recall = {}
        complete = {}
        for k in (1, 3, 5, 10):
            candidates = set(retrieved[:k])
            found_docs = {docs[id_] for id_ in candidates}
            document_recall[str(k)] = round(
                len(gold_documents & found_docs) / len(gold_documents), 6
            )
            complete[str(k)] = expected.issubset(candidates)
        missing = sorted(expected.difference(retrieved))
        nearest = next((h.rank for h in hits if h.document_id in gold_documents), None)
        if not missing:
            failure = "all_gold_evidence_retrieved"
        elif nearest is not None:
            failure = "gold_document_found_exact_chunk_missing"
        else:
            failure = "gold_document_missing"
        answers.append(
            {
                "case_id": case["case_id"],
                "language": case["language"],
                "gold_chunk_count": len(expected),
                "gold_document_count": len(gold_documents),
                "gold_chunk_ranks_at_10": gold_ranks,
                "first_gold_document_rank": nearest,
                "document_recall_at_k": document_recall,
                "all_gold_evidence_at_k": complete,
                "evidence_recall_at_k": row["recall"],
                "error_class": failure,
                "missing_gold_chunk_ids_at_10": missing,
            }
        )
    n = len(answers)
    ordered = sorted(durations)

    def percentile(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * q))], 4)

    return {
        "schema_version": 1,
        "evaluation": "s22-v0.1-frozen-diagnostics",
        "retriever": baseline["retriever"],
        "corpus_sha256": EXPECTED_CHUNKS_SHA256,
        "gold_sha256": GOLD_SHA256,
        "answerable_cases": n,
        "unanswerable_cases_excluded": baseline["unanswerable_cases_excluded"],
        "evidence_recall_at_k": baseline["recall_at_k"],
        "mrr_at_10": baseline["mrr_at_max_k"],
        "document_recall_at_k": {
            str(k): round(statistics.mean(r["document_recall_at_k"][str(k)] for r in answers), 6)
            for k in (1, 3, 5, 10)
        },
        "complete_evidence_coverage_at_k": {
            str(k): round(sum(r["all_gold_evidence_at_k"][str(k)] for r in answers) / n, 6)
            for k in (1, 3, 5, 10)
        },
        "latency": {
            "scope": "warm in-memory search only; excludes index build and I/O",
            "samples": len(durations),
            "repetitions_per_question": repetitions,
            "unit": "ms",
            "median": percentile(0.5),
            "p95": percentile(0.95),
        },
        "cases": answers,
        "notes": "All-evidence coverage requires every Gold chunk, not just one. Document-level hits do not substitute for evidence recall. This small pilot is diagnostic, not a generalization estimate.",
    }


def diagnose_from_files(
    chunks: Path, gold: Path, output: Path, *, repetitions: int = 7
) -> dict[str, Any]:
    from p2_research.application.retrieval.bm25 import load_locked_chunks

    report = diagnose(
        BM25Index(load_locked_chunks(chunks)), gold.read_bytes(), repetitions=repetitions
    )
    if output.is_symlink():
        raise RetrievalError("refusing output symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="S2.2 frozen retrieval diagnostics")
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/generated/retrieval/s22-diagnostics.json")
    )
    parser.add_argument("--repetitions", type=int, default=7)
    args = parser.parse_args()
    report = diagnose_from_files(args.chunks, args.gold, args.output, repetitions=args.repetitions)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "evidence_recall_at_k",
                    "document_recall_at_k",
                    "complete_evidence_coverage_at_k",
                    "mrr_at_10",
                    "latency",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
