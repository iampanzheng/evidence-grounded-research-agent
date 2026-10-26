"""Read-only failure audit of a *real* frozen Dense/RRF benchmark.

This is not another semantic experiment: no embeddings are generated here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from p2_research.application.retrieval.benchmark import GOLD_SHA256, write_report
from p2_research.application.retrieval.bm25 import (
    EXPECTED_CHUNKS_SHA256,
    RetrievalError,
    load_locked_chunks,
)

VARIANTS = ("bm25", "dense", "hybrid_rrf")
EXPECTED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def analyze(
    report: dict[str, Any], chunks: list[dict[str, Any]], gold_bytes: bytes
) -> dict[str, Any]:
    if hashlib.sha256(gold_bytes).hexdigest() != GOLD_SHA256:
        raise RetrievalError("GOLD_DATASET_DRIFT")
    if (
        report.get("corpus_sha256") != EXPECTED_CHUNKS_SHA256
        or report.get("gold_sha256") != GOLD_SHA256
        or report.get("embedding_mode") != "real_sentence_transformer"
        or report.get("model_name") != EXPECTED_MODEL
    ):
        raise RetrievalError("UNVERIFIED_REAL_MODEL_REPORT")
    if len(chunks) != 409 or len({c["chunk_id"] for c in chunks}) != 409:
        raise RetrievalError("INVALID_FROZEN_CHUNKS")
    if set(report.get("variants", {})) != set(VARIANTS) or report.get("answerable_cases") != 9:
        raise RetrievalError("INCOMPLETE_VARIANT_REPORT")
    gold = json.loads(gold_bytes)
    cases = {c["case_id"]: c for c in gold["cases"] if c["answerability"] == "answerable"}
    chunk_by_id = {c["chunk_id"]: c for c in chunks}
    result_maps: dict[str, dict[str, dict[str, Any]]] = {}
    for name in VARIANTS:
        rows = report["variants"][name]["case_results"]
        if len(rows) != 9 or {r["case_id"] for r in rows} != set(cases):
            raise RetrievalError("INCOMPLETE_CASE_RESULTS")
        result_maps[name] = {r["case_id"]: r for r in rows}
        for row in rows:
            expected = len(cases[row["case_id"]]["gold_evidence"])
            recall = float(row["recall_at_10"])
            if not 0 <= recall <= 1 or abs(round(recall * expected) - recall * expected) > 1e-5:
                raise RetrievalError("INVALID_EVIDENCE_RECALL")
            rank = row["first_relevant_rank"]
            if (rank is None) != (recall == 0) or (rank is not None and not 1 <= rank <= 10):
                raise RetrievalError("INCONSISTENT_EVIDENCE_RANK")
    case_findings = []
    for case_id, case in sorted(cases.items()):
        evidence = [chunk_by_id[e["chunk_id"]] for e in case["gold_evidence"]]
        ranks = {name: result_maps[name][case_id]["first_relevant_rank"] for name in VARIANTS}
        recalls = {name: result_maps[name][case_id]["recall_at_10"] for name in VARIANTS}
        case_findings.append(
            {
                "case_id": case_id,
                "language": case["language"],
                "gold_chunks": len(evidence),
                "gold_document_count": len({c["document_id"] for c in evidence}),
                "gold_chunk_utf8_bytes": [len(c["text"].encode("utf-8")) for c in evidence],
                "gold_chunk_characters": [len(c["text"]) for c in evidence],
                "first_relevant_rank": ranks,
                "evidence_recall_at_10": recalls,
                "dense_vs_bm25": (
                    "regression"
                    if recalls["dense"] < recalls["bm25"]
                    else "gain"
                    if recalls["dense"] > recalls["bm25"]
                    else "tie"
                ),
                "hybrid_vs_bm25": (
                    "regression"
                    if recalls["hybrid_rrf"] < recalls["bm25"]
                    else "gain"
                    if recalls["hybrid_rrf"] > recalls["bm25"]
                    else "tie"
                ),
                "hybrid_rank_regression": (
                    ranks["bm25"] is not None
                    and (ranks["hybrid_rrf"] is None or ranks["hybrid_rrf"] > ranks["bm25"])
                ),
            }
        )
    return {
        "schema_version": 1,
        "experiment": "s24-v02-dense-failure-audit",
        "source_experiment": report["experiment"],
        "model_name": report["model_name"],
        "model_revision": "NOT_RECORDED_IN_V01",
        "tokenizer_exact_truncation": "NOT_MEASURED",
        "interpretation": "Byte/character lengths are proxies, not transformer token counts or proof of truncation.",
        "corpus_sha256": EXPECTED_CHUNKS_SHA256,
        "gold_sha256": GOLD_SHA256,
        "metrics": {
            name: {
                "recall_at_10": report["variants"][name]["recall_at_k"]["10"],
                "mrr_at_10": report["variants"][name]["mrr_at_10"],
            }
            for name in VARIANTS
        },
        "cases": case_findings,
        "failure_counts": {
            "dense_evidence_regression": sum(
                c["dense_vs_bm25"] == "regression" for c in case_findings
            ),
            "hybrid_evidence_regression": sum(
                c["hybrid_vs_bm25"] == "regression" for c in case_findings
            ),
            "hybrid_first_rank_regression": sum(c["hybrid_rank_regression"] for c in case_findings),
        },
        "follow_up": "Measure exact tokenizer truncation and pin model revision during next real-model run; do not infer from character counts.",
        "contains_source_text": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit real S2.4 v0.1 Dense/RRF failures")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/generated/retrieval/s24-v02-failures.json")
    )
    args = parser.parse_args()
    result = analyze(
        json.loads(args.input.read_text(encoding="utf-8")),
        load_locked_chunks(args.chunks),
        args.gold.read_bytes(),
    )
    write_report(args.output, result)
    print(
        json.dumps(
            {"failure_counts": result["failure_counts"], "case_count": len(result["cases"])},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
