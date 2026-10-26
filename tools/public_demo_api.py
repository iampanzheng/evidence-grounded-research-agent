"""Opt-in, synthetic-only public P2 demo. Never fetches a real corpus or calls Groq."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from p2_research.application.research.llm import ChatCompletion
from p2_research.application.retrieval.bm25 import BM25Index
from p2_research.infrastructure.serving.api import APISettings, create_app
from p2_research.infrastructure.serving.contract import ResearchServingService
from p2_research.infrastructure.workflows.bounded import ResearchWorkflow, WorkflowConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "public_demo" / "synthetic_chunks.json"
DATASET_ID = "public-synthetic-orion-v1"


def load_synthetic_fixture(path: Path = FIXTURE) -> list[dict[str, Any]]:
    """Audit ORIGINAL public fixture bytes; do not weaken the locked private loader."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("synthetic") is not True or obj.get("dataset_id") != DATASET_ID:
        raise ValueError("INVALID_PUBLIC_DEMO_DATASET_ID")
    chunks = obj.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != 4:
        raise ValueError("INVALID_PUBLIC_DEMO_CHUNK_COUNT")
    found: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("INVALID_PUBLIC_DEMO_CHUNK")
        src = chunk.get("source_path", "")
        if not isinstance(src, str) or not src.startswith("public_demo/documents/"):
            raise ValueError("INVALID_PUBLIC_DEMO_SOURCE_PATH")
        file = ROOT / src
        if (
            not file.is_file()
            or file.is_symlink()
            or not file.resolve().is_relative_to(ROOT / "public_demo" / "documents")
        ):
            raise ValueError("PUBLIC_DEMO_SOURCE_MISSING")
        original = file.read_bytes()
        text = chunk.get("text")
        if not isinstance(text, str) or text.encode("utf-8") != original:
            raise ValueError("PUBLIC_DEMO_SOURCE_DRIFT")
        digest = hashlib.sha256(original).hexdigest()
        if (
            chunk.get("source_sha256") != digest
            or chunk.get("text_sha256") != digest
            or chunk.get("start_byte") != 0
            or chunk.get("end_byte") != len(original)
            or chunk.get("snapshot_id") != DATASET_ID
            or chunk.get("source_url") != ""
        ):
            raise ValueError("PUBLIC_DEMO_PROVENANCE_DRIFT")
        cid = chunk.get("chunk_id")
        if not isinstance(cid, str) or cid in found or not cid.startswith("public_chunk_"):
            raise ValueError("PUBLIC_DEMO_CHUNK_ID_INVALID")
        found.add(cid)
    return chunks


class SyntheticDemoClient:
    """Deterministic canned fixture response, NOT a factual LLM evaluation."""

    def complete(self, messages: list[dict[str, str]]) -> ChatCompletion:
        question = (
            messages[-1]["content"]
            .split("QUESTION:\n", 1)[-1]
            .split("\n\nUNTRUSTED EVIDENCE:", 1)[0]
        )
        if "rollback" in question.lower() or "release" in question.lower():
            content = {
                "status": "ANSWERED",
                "answer": "The fictional Orion release requires passing integration tests, a rollback plan, and an approved change ticket [E1].",
                "citations": ["E1"],
            }
        else:
            content = {"status": "INSUFFICIENT_EVIDENCE", "answer": "", "citations": []}
        return ChatCompletion(content=json.dumps(content), model="synthetic-canned-response")


def load_public_demo_service(_settings: APISettings) -> ResearchServingService:
    chunks = load_synthetic_fixture()
    return ResearchServingService(
        ResearchWorkflow(
            BM25Index(chunks),
            chunks,
            SyntheticDemoClient(),
            config=WorkflowConfig(deadline_seconds=15),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="PUBLIC SYNTHETIC OFFLINE DEMO ONLY")
    parser.add_argument("--demo-only", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    # Remove any remote credentials in this process. This app uses a fake client only.
    for key in list(os.environ):
        if key.startswith(("GROQ_", "P2_LLM_")):
            os.environ.pop(key, None)
    settings = APISettings.from_env()
    import uvicorn

    uvicorn.run(
        create_app(settings, loader=load_public_demo_service),
        host="127.0.0.1",
        port=args.port,
        workers=1,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
