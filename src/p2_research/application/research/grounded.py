"""S3.1 deterministic evidence-bound extractive research contract.

This is NOT an LLM, semantic sufficiency classifier, or factuality guarantee.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

from p2_research.application.retrieval.benchmark import SearchIndex
from p2_research.domain.models import ResearchStatus


class GroundingError(ValueError):
    """Invalid or unverifiable evidence provenance."""


@dataclass(frozen=True, slots=True)
class Evidence:
    citation_id: str
    chunk_id: str
    document_id: str
    source_path: str
    source_url: str
    source_sha256: str
    snapshot_id: str
    start_byte: int
    end_byte: int
    text_sha256: str
    text: str

    def public_dict(self) -> dict[str, str | int]:
        """Only identifiers and hashes; private text is never exported."""
        result = asdict(self)
        result.pop("text")
        return result


@dataclass(frozen=True, slots=True)
class ResearchAnswer:
    question: str
    status: ResearchStatus
    answer: str
    citations: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    generator: str = "deterministic-extractive-v0.1"

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "question": self.question,
            "status": self.status.value,
            "answer": "[REDACTED: private source excerpt]" if self.citations else self.answer,
            "citations": list(self.citations),
            "evidence": [item.public_dict() for item in self.evidence],
            "generator": self.generator,
            "caveat": "Extractive prototype; citations identify retrieved chunks, not verified claim entailment.",
        }


def prepare_evidence(
    question: str, index: SearchIndex, chunks: list[dict[str, Any]], *, top_k: int = 5
) -> tuple[Evidence, ...]:
    if not question.strip() or not 1 <= top_k <= 15:
        raise GroundingError("nonempty question and top_k between 1 and 15 required")
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    if len(by_id) != len(chunks):
        raise GroundingError("duplicate chunk IDs")
    result: list[Evidence] = []
    seen: set[str] = set()
    for hit in index.search(question, top_k):
        if hit.chunk_id in seen:
            raise GroundingError("duplicate retrieval hit")
        seen.add(hit.chunk_id)
        chunk = by_id.get(hit.chunk_id)
        if chunk is None:
            raise GroundingError("retrieved chunk absent from manifest")
        for key in ("document_id", "source_path", "source_url"):
            if getattr(hit, key) != chunk[key]:
                raise GroundingError(f"retrieval provenance mismatch: {key}")
        raw = chunk["text"].encode("utf-8")
        if len(raw) != chunk["end_byte"] - chunk["start_byte"]:
            raise GroundingError("chunk byte span mismatch")
        result.append(
            Evidence(
                citation_id=f"E{len(result) + 1}",
                chunk_id=chunk["chunk_id"],
                document_id=chunk["document_id"],
                source_path=chunk["source_path"],
                source_url=chunk["source_url"],
                source_sha256=chunk["source_sha256"],
                snapshot_id=chunk["snapshot_id"],
                start_byte=chunk["start_byte"],
                end_byte=chunk["end_byte"],
                text_sha256=hashlib.sha256(raw).hexdigest(),
                text=chunk["text"],
            )
        )
    return tuple(result)


# Conservative, explicitly scoped rule for production-state questions. General
# documentation does not establish live deployment configuration. Not a semantic model.
DEPLOYMENT_RE = re.compile(r"\b(specific|production|currently|deployment|runtime)\b", re.I)


def answer_question(
    question: str, index: SearchIndex, chunks: list[dict[str, Any]], *, top_k: int = 5
) -> ResearchAnswer:
    """Return an auditable excerpt or abstain; never synthesize unsupported claims."""
    evidence = prepare_evidence(question, index, chunks, top_k=top_k)
    if DEPLOYMENT_RE.search(question):
        return ResearchAnswer(
            question,
            ResearchStatus.INSUFFICIENT_EVIDENCE,
            "The pinned general documentation cannot verify deployment-specific runtime facts.",
            (),
            evidence,
        )
    if not evidence:
        return ResearchAnswer(
            question,
            ResearchStatus.INSUFFICIENT_EVIDENCE,
            "No supporting evidence was retrieved.",
            (),
            (),
        )
    # Explicit evidence excerpt, not a generated declarative answer. Do not claim
    # the retrieved chunk necessarily answers the question.
    excerpt = evidence[0].text.strip()[:500]
    return ResearchAnswer(
        question,
        ResearchStatus.VERIFICATION_FAILED,
        f"Retrieved evidence excerpt (answer not yet verified): {excerpt} [E1]",
        ("E1",),
        evidence,
    )


def validate_citations(result: ResearchAnswer) -> None:
    known = {item.citation_id for item in result.evidence}
    if len(known) != len(result.evidence) or not set(result.citations) <= known:
        raise GroundingError("missing or duplicate citation reference")
    if result.status == ResearchStatus.INSUFFICIENT_EVIDENCE and result.citations:
        raise GroundingError("abstention must not assert supporting citations")
    for item in result.evidence:
        if hashlib.sha256(item.text.encode("utf-8")).hexdigest() != item.text_sha256:
            raise GroundingError("evidence text hash mismatch")
