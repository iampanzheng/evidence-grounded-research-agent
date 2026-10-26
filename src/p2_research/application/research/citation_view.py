"""Public-facing citation links: mechanical attribution, never semantic verification.

No citation is inferred from text similarity or from an uncited sentence.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from p2_research.application.research.grounded import ResearchAnswer, validate_citations
from p2_research.domain.models import ResearchStatus

_CITE = re.compile(r"\[(E[1-9][0-9]*)\]")


def build_user_citation_view(result: ResearchAnswer, citation_provenance: str) -> dict[str, Any]:
    """Return a display model with source links and an explicit quality disclaimer.

    Caller must supply the validated ResearchAnswer, not unchecked model JSON.
    Source bodies and private chunk text are intentionally excluded.
    """
    validate_citations(result)
    if result.status != ResearchStatus.ANSWERED:
        return {
            "status": result.status.value,
            "answer": result.answer,
            "sources": [],
            "citation_provenance": citation_provenance,
            "claim_support": "NOT_VERIFIED",
        }
    cited = set(result.citations)
    inline = set(_CITE.findall(result.answer))
    if not cited or cited != inline:
        raise ValueError("answer citations do not match validated inline references")
    if citation_provenance not in {"MODEL_INLINE", "SYSTEM_APPENDED_SINGLE_SENTENCE"}:
        raise ValueError("missing or invalid citation attribution for answered result")
    sources: list[dict[str, str]] = []
    for item in result.evidence:
        if item.citation_id not in cited:
            continue
        parts = urlsplit(item.source_url)
        if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
            raise ValueError("source URL must be an HTTPS reference")
        sources.append(
            {
                "citation_id": item.citation_id,
                "source_path": item.source_path,
                "source_url": item.source_url,
                "document_id": item.document_id,
                "chunk_id": item.chunk_id,
            }
        )
    return {
        "status": result.status.value,
        "answer": result.answer,
        "sources": sources,
        "citation_provenance": citation_provenance,
        "claim_support": "NOT_VERIFIED",
        "note": "Links identify cited source chunks; factual entailment requires separate review.",
    }
