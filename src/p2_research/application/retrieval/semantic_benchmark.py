"""Run real optional multilingual dense and hybrid frozen-pilot comparisons."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from p2_research.application.retrieval.benchmark import benchmark, write_report
from p2_research.application.retrieval.bm25 import BM25Index, load_locked_chunks
from p2_research.application.retrieval.semantic import (
    DenseIndex,
    HybridIndex,
    SentenceTransformerEmbedder,
)

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def compare(
    chunks: list[dict[str, Any]], gold_bytes: bytes, model_name: str, *, embedder: Any = None
) -> dict[str, Any]:
    if len(chunks) != 409:
        raise ValueError("frozen 409-chunk corpus required")
    start = time.perf_counter()
    lexical = BM25Index(chunks)
    lexical_build_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    dense = DenseIndex(
        chunks, embedder if embedder is not None else SentenceTransformerEmbedder(model_name)
    )
    dense_build_ms = (time.perf_counter() - start) * 1000
    variants: dict[str, dict[str, Any]] = {}
    for name, index in (
        ("bm25", lexical),
        ("dense", dense),
        ("hybrid_rrf", HybridIndex(lexical, dense)),
    ):
        start = time.perf_counter()
        result = benchmark(index, gold_bytes)
        query_ms = (time.perf_counter() - start) * 1000
        variants[name] = {
            "recall_at_k": result["recall_at_k"],
            "mrr_at_10": result["mrr_at_max_k"],
            "case_results": [
                {
                    "case_id": case["case_id"],
                    "first_relevant_rank": case["first_relevant_rank"],
                    "recall_at_10": case["recall"]["10"],
                }
                for case in result["cases"]
            ],
            "total_query_time_ms": round(query_ms, 3),
        }
    return {
        "schema_version": 1,
        "experiment": "s24-v01-dense-hybrid",
        "model_name": model_name,
        "embedding_mode": "real_sentence_transformer"
        if embedder is None
        else "injected_test_embedder",
        "corpus_sha256": result["corpus_sha256"],
        "gold_sha256": result["gold_sha256"],
        "answerable_cases": result["answerable_cases"],
        "build_ms": {"bm25": round(lexical_build_ms, 3), "dense": round(dense_build_ms, 3)},
        "variants": variants,
        "caveat": "Exploratory signed pilot only; 9 answerable cases. No model selection without held-out evaluation. Query time includes Python and embedding runtime, not network/model download.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="S2.4 real-model dense and RRF hybrid experiment")
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--output", type=Path, default=Path("reports/generated/retrieval/s24-dense-hybrid.json")
    )
    args = parser.parse_args()
    result = compare(load_locked_chunks(args.chunks), args.gold.read_bytes(), args.model)
    write_report(args.output, result)
    print(
        json.dumps(
            {
                k: {"recall_at_10": v["recall_at_k"]["10"], "mrr_at_10": v["mrr_at_10"]}
                for k, v in result["variants"].items()
            }
        )
    )


if __name__ == "__main__":
    main()
