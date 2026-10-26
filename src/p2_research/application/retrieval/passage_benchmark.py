"""Real-model token-aware passage comparison, without altering frozen Gold."""

from __future__ import annotations

import argparse
import json
import time
from importlib import import_module
from pathlib import Path
from typing import Any

from p2_research.application.retrieval.benchmark import benchmark, write_report
from p2_research.application.retrieval.bm25 import BM25Index, load_locked_chunks
from p2_research.application.retrieval.passage import PassageDenseIndex
from p2_research.application.retrieval.semantic import DenseIndex, HybridIndex
from p2_research.application.retrieval.semantic_benchmark import DEFAULT_MODEL


def compare_passages(
    chunks: list[dict[str, Any]],
    gold_bytes: bytes,
    embedder: Any,
    *,
    tokenizer: Any,
    max_sequence_length: int,
    overlap: int = 16,
    model_name: str,
    model_revision: str | None,
) -> dict[str, Any]:
    start = time.perf_counter()
    lexical = BM25Index(chunks)
    lexical_build_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    original = DenseIndex(chunks, embedder)
    original_build_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    passage = PassageDenseIndex(
        chunks,
        embedder,
        tokenizer=tokenizer,
        max_sequence_length=max_sequence_length,
        overlap=overlap,
    )
    passage_build_ms = (time.perf_counter() - start) * 1000
    variants = {}
    identity: dict[str, Any] = {}
    for name, retriever in (
        ("bm25", lexical),
        ("dense_original", original),
        ("hybrid_original", HybridIndex(lexical, original)),
        ("dense_passages", passage),
        ("hybrid_passages", HybridIndex(lexical, passage)),
    ):
        start = time.perf_counter()
        measured = benchmark(retriever, gold_bytes)
        query_ms = (time.perf_counter() - start) * 1000
        identity = measured
        variants[name] = {
            "recall_at_k": measured["recall_at_k"],
            "mrr_at_10": measured["mrr_at_max_k"],
            "total_query_time_ms": round(query_ms, 3),
            "case_results": [
                {
                    "case_id": case["case_id"],
                    "first_relevant_rank": case["first_relevant_rank"],
                    "recall_at_10": case["recall"]["10"],
                }
                for case in measured["cases"]
            ],
        }
    return {
        "schema_version": 1,
        "experiment": "s24-v04-token-aware-passage-retrieval",
        "model_name": model_name,
        "model_revision": model_revision or "UNRESOLVED",
        "corpus_sha256": identity["corpus_sha256"],
        "gold_sha256": identity["gold_sha256"],
        "answerable_cases": identity["answerable_cases"],
        "passage_count": passage.passage_count,
        "parent_chunk_count": len(chunks),
        "model_max_sequence_length": max_sequence_length,
        "overlap_tokens": overlap,
        "build_ms": {
            "bm25": round(lexical_build_ms, 3),
            "dense_original": round(original_build_ms, 3),
            "dense_passages": round(passage_build_ms, 3),
        },
        "variants": variants,
        "contains_source_text": False,
        "caveat": "Exploratory pilot only; never choose model from these 9 repeatedly inspected Gold cases.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Token-aware passage retrieval comparison")
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--overlap", type=int, default=16)
    parser.add_argument(
        "--output", type=Path, default=Path("reports/generated/retrieval/s24-v04-passages.json")
    )
    args = parser.parse_args()
    SentenceTransformer = import_module("sentence_transformers").SentenceTransformer
    model = SentenceTransformer(args.model, revision=args.revision)
    max_length = model.get_max_seq_length()
    if not isinstance(max_length, int):
        raise ValueError("model input sequence length unavailable")

    class Adapter:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return model.encode(texts, show_progress_bar=False, convert_to_numpy=True).tolist()  # type: ignore[no-any-return]

    result = compare_passages(
        load_locked_chunks(args.chunks),
        args.gold.read_bytes(),
        Adapter(),
        tokenizer=model.tokenizer,
        max_sequence_length=max_length,
        overlap=args.overlap,
        model_name=args.model,
        model_revision=args.revision,
    )
    write_report(args.output, result)
    print(
        json.dumps(
            {
                name: {"recall_at_10": value["recall_at_k"]["10"], "mrr_at_10": value["mrr_at_10"]}
                for name, value in result["variants"].items()
            }
        )
    )


if __name__ == "__main__":
    main()
