"""Non-mutating, frozen-pilot lexical ablations for S2.3."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from p2_research.application.retrieval.benchmark import benchmark, write_report
from p2_research.application.retrieval.bm25 import (
    BM25Index,
    RetrievalError,
    load_locked_chunks,
    tokenize,
)

CHINESE_RE = re.compile(r"[\u3400-\u9fff]+")


def tokenize_trigrams(text: str) -> list[str]:
    """Baseline Chinese 1/2-grams plus 3-grams; unchanged English tokens."""
    terms = tokenize(text)
    for match in CHINESE_RE.finditer(text):
        run = match.group()
        terms.extend(run[i : i + 3] for i in range(len(run) - 2))
    return terms


def augment_headings(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """In-memory headings only; preserve chunk identities, offsets and locked source text."""
    augmented = []
    for chunk in chunks:
        copy = dict(chunk)
        headings = chunk.get("heading_path", [])
        if not isinstance(headings, list) or any(not isinstance(h, str) for h in headings):
            raise RetrievalError("invalid heading path")
        copy["text"] = chunk["text"] + "\n" + " ".join(headings)
        augmented.append(copy)
    return augmented


def compare(chunks: list[dict[str, Any]], gold_bytes: bytes) -> dict[str, Any]:
    """Evaluate fixed ablations, not a post-hoc parameter search."""
    if len(chunks) != 409:
        raise RetrievalError("expected frozen 409-chunk corpus")
    configs: tuple[tuple[str, bool, Callable[[str], list[str]]], ...] = (
        ("baseline", False, tokenize),
        ("cn_trigrams", False, tokenize_trigrams),
        ("heading_context", True, tokenize),
        ("heading_and_cn_trigrams", True, tokenize_trigrams),
    )
    results = {}
    for name, use_headings, tokenizer in configs:
        material = augment_headings(chunks) if use_headings else chunks
        result = benchmark(BM25Index(material, tokenizer=tokenizer), gold_bytes)
        results[name] = {
            "recall_at_k": result["recall_at_k"],
            "mrr_at_10": result["mrr_at_max_k"],
            "cases": [
                {
                    "case_id": case["case_id"],
                    "first_relevant_rank": case["first_relevant_rank"],
                    "recall_at_10": case["recall"]["10"],
                }
                for case in result["cases"]
            ],
        }
    return {
        "schema_version": 1,
        "evaluation": "s23-v01-lexical-ablation",
        "corpus_sha256": result["corpus_sha256"],
        "gold_sha256": result["gold_sha256"],
        "pilot_answerable_cases": result["answerable_cases"],
        "variants": results,
        "caveat": (
            "Frozen 9-answerable-case pilot is reused for exploratory design only. "
            "No statistical generalization or final model selection is justified. "
            "Heading augmentation is in-memory and not a new frozen corpus."
        ),
    }


def compare_from_files(chunks: Path, gold: Path, output: Path) -> dict[str, Any]:
    report = compare(load_locked_chunks(chunks), gold.read_bytes())
    write_report(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="S2.3 frozen lexical retrieval experiments")
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/datasets/pilot-gold-v1.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/generated/retrieval/s23-experiments.json")
    )
    args = parser.parse_args()
    report = compare_from_files(args.chunks, args.gold, args.output)
    print(
        json.dumps(
            {
                k: {"recall_at_10": v["recall_at_k"]["10"], "mrr_at_10": v["mrr_at_10"]}
                for k, v in report["variants"].items()
            }
        )
    )


if __name__ == "__main__":
    main()
