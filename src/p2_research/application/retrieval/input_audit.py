"""Measure actual tokenizer pre-truncation lengths for the locked corpus.

This reports token-count risks, not a proven causal explanation of poor retrieval.
No source text or token IDs leave the machine in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from p2_research.application.retrieval.bm25 import (
    EXPECTED_CHUNKS_SHA256,
    RetrievalError,
    load_locked_chunks,
)
from p2_research.application.retrieval.semantic_benchmark import DEFAULT_MODEL

EXPECTED_GOLD_SHA256 = "7975fb03318da9cd573ac2f681ce1e0af92376ac449b4aa989ef58d3f089f8ce"


def analyze_tokens(
    chunks: list[dict[str, Any]],
    gold_bytes: bytes,
    *,
    tokenizer: Callable[..., Any],
    max_sequence_length: int,
    model_name: str,
    model_revision: str | None,
    tokenizer_name: str | None = None,
) -> dict[str, Any]:
    """Read full (untruncated) token sequences including special tokens."""
    if len(chunks) != 409 or not isinstance(max_sequence_length, int) or max_sequence_length < 2:
        raise RetrievalError("frozen corpus and positive sequence limit required")
    if hashlib.sha256(gold_bytes).hexdigest() != EXPECTED_GOLD_SHA256:
        raise RetrievalError("frozen Gold SHA-256 mismatch")
    gold = json.loads(gold_bytes)
    if gold["corpus"]["chunks_sha256"] != EXPECTED_CHUNKS_SHA256:
        raise RetrievalError("Gold corpus identity mismatch")
    cid_map = {c["chunk_id"]: c for c in chunks}
    if len(cid_map) != 409:
        raise RetrievalError("duplicate chunk IDs")

    def count_tokens(text: str) -> int:
        result = tokenizer(text, add_special_tokens=True, truncation=False)
        token_ids = result["input_ids"]
        if not isinstance(token_ids, list) or any(not isinstance(x, int) for x in token_ids):
            raise RetrievalError("tokenizer did not return a flat sequence of token IDs")
        return len(token_ids)

    counts = {cid: count_tokens(str(c["text"])) for cid, c in cid_map.items()}
    cases: list[dict[str, Any]] = []
    for case in gold["cases"]:
        question_tokens = count_tokens(case["question"])
        evidence = [e["chunk_id"] for e in case["gold_evidence"]]
        if any(cid not in counts for cid in evidence):
            raise RetrievalError("Gold references unknown chunk")
        cases.append(
            {
                "case_id": case["case_id"],
                "answerability": case["answerability"],
                "query_tokens": question_tokens,
                "query_exceeds_limit": question_tokens > max_sequence_length,
                "gold_chunks": [
                    {
                        "chunk_id": cid,
                        "tokens": counts[cid],
                        "exceeds_limit": counts[cid] > max_sequence_length,
                    }
                    for cid in evidence
                ],
            }
        )
    risky = sum(n > max_sequence_length for n in counts.values())
    return {
        "schema_version": 1,
        "experiment": "s24-v03-model-input-truncation-audit",
        "corpus_sha256": EXPECTED_CHUNKS_SHA256,
        "gold_sha256": EXPECTED_GOLD_SHA256,
        "model_name": model_name,
        "model_revision": model_revision or "UNRESOLVED",
        "tokenizer_name": tokenizer_name or "UNRESOLVED",
        "max_sequence_length": max_sequence_length,
        "chunk_count": len(counts),
        "chunks_exceeding_limit": risky,
        "chunks_within_limit": len(counts) - risky,
        "max_chunk_tokens": max(counts.values()),
        "cases": cases,
        "contains_source_text": False,
        "interpretation": "Exact tokenizer full-length token counts; exceeding the model limit indicates truncation risk, not proven retrieval failure causation.",
    }


def _model_metadata(model: Any) -> tuple[str | None, str | None]:
    """Read commit from the loaded transformer config rather than guessing it."""
    try:
        first = model._first_module()
        auto_model = first.auto_model
        commit = getattr(auto_model.config, "_commit_hash", None)
        tokenizer = first.tokenizer
        name = getattr(tokenizer, "name_or_path", None)
    except (AttributeError, TypeError):
        return None, None
    return (str(commit) if commit else None, str(name) if name else None)


def main() -> None:
    parser = argparse.ArgumentParser(description="S2.4 v0.3 actual tokenizer input audit")
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--revision", default=None, help="Optional pinned HF model commit; do not invent"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("reports/generated/retrieval/s24-v03-input-audit.json")
    )
    args = parser.parse_args()
    try:
        from importlib import import_module

        SentenceTransformer = import_module("sentence_transformers").SentenceTransformer
    except ImportError as exc:
        raise SystemExit("Install semantic extra first: uv sync --extra semantic") from exc
    model = SentenceTransformer(args.model, revision=args.revision)
    max_length = model.get_max_seq_length()
    if not isinstance(max_length, int):
        raise RetrievalError("loaded model did not provide max sequence length")
    revision, tokenizer_name = _model_metadata(model)
    if args.revision is not None and revision is not None and revision != args.revision:
        raise RetrievalError("loaded model commit does not match pinned revision")
    result = analyze_tokens(
        load_locked_chunks(args.chunks),
        args.gold.read_bytes(),
        tokenizer=model.tokenizer,
        max_sequence_length=max_length,
        model_name=args.model,
        model_revision=revision or args.revision,
        tokenizer_name=tokenizer_name,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "model_revision": result["model_revision"],
                "max_sequence_length": max_length,
                "chunks_exceeding_limit": result["chunks_exceeding_limit"],
                "gold_cases_with_risky_chunks": [
                    c["case_id"]
                    for c in result["cases"]
                    if any(g["exceeds_limit"] for g in c["gold_chunks"])
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
