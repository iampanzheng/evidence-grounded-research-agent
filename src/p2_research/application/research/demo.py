"""Local private-corpus S3.1 demonstration; never writes raw evidence to a report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from p2_research.application.research.grounded import answer_question, validate_citations
from p2_research.application.retrieval.bm25 import BM25Index, load_locked_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="S3.1 deterministic grounded research prototype")
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    chunks = load_locked_chunks(args.chunks)
    result = answer_question(args.question, BM25Index(chunks), chunks, top_k=args.top_k)
    validate_citations(result)
    # Intentionally metadata only; excerpt in answer could redistribute raw text.
    print(json.dumps(result.public_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
