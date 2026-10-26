"""Opt-in local LLM demo; --user-view prints answer, never original evidence text."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from p2_research.application.research.citation_view import build_user_citation_view
from p2_research.application.research.llm import generate_answer
from p2_research.application.retrieval.bm25 import BM25Index, load_locked_chunks
from p2_research.infrastructure.llm.openai_compatible import OpenAICompatibleChatClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in S3.2 real LLM answer experiment")
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--chunks", type=Path, default=Path("reports/generated/corpus/bootstrap/chunks.json")
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--user-view", action="store_true", help="Show cited answer and URLs locally"
    )
    args = parser.parse_args()
    chunks = load_locked_chunks(args.chunks)
    client = OpenAICompatibleChatClient.from_env()
    run = generate_answer(args.question, BM25Index(chunks), chunks, client, top_k=args.top_k)
    # Answer and all original source text may be covered by restricted publication policy.
    output = (
        build_user_citation_view(run.result, run.citation_provenance)
        if args.user_view
        else run.public_dict()
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
