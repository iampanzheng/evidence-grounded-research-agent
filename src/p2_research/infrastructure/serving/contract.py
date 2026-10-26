"""Stable public HTTP payloads over the accepted Stage 5 research workflow.

No FastAPI or web dependencies belong in this application-layer contract.
Only an authenticated future API adapter may call this facade; request admission,
rate limiting, CORS and authentication are the responsibility of that adapter.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Protocol, TypedDict
from urllib.parse import urlsplit

from p2_research.domain.models import ResearchStatus
from p2_research.infrastructure.workflows.bounded import WorkflowResult

MAX_QUESTION_CHARS = 2000
_REQUEST_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ServingWorkflow(Protocol):
    def invoke(self, question: str) -> WorkflowResult: ...


class CitationPayload(TypedDict):
    id: str
    document_id: str
    chunk_id: str
    source_url: str


class ResearchPayload(TypedDict):
    schema_version: int
    request_id: str
    status: str
    answer: str
    reason_code: str
    citations: list[CitationPayload]
    claim_support: str


@dataclass(frozen=True)
class ServingResult:
    http_status: int
    payload: ResearchPayload


class InvalidRequest(ValueError):
    """Caller receives a fixed, non-sensitive HTTP 400 response."""


def validate_question(question: object) -> str:
    if not isinstance(question, str) or not question.strip():
        raise InvalidRequest("INVALID_QUESTION")
    if len(question) > MAX_QUESTION_CHARS or "\x00" in question:
        raise InvalidRequest("INVALID_QUESTION")
    return question.strip()


def validate_request_id(request_id: object) -> str:
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise InvalidRequest("INVALID_REQUEST_ID")
    return request_id


def _safe_public_source_url(value: str) -> str:
    """Only publish safe absolute HTTPS links; never reflect URL credentials."""
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port == 0
        ):
            return ""
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass  # DNS names are permitted; this function never fetches them.
        else:
            if not address.is_global:
                return ""
    except ValueError:
        return ""
    return value


def present_result(result: WorkflowResult, *, request_id: str) -> ServingResult:
    """Return the validated answer, not the redacted operational diagnostic.

    Provenance is limited to identifiers and public source URLs; chunk text,
    prompts, model raw responses, paths and credentials never cross this boundary.
    """
    validate_request_id(request_id)
    status = result.result.status
    answered = status == ResearchStatus.ANSWERED
    refused = status == ResearchStatus.INSUFFICIENT_EVIDENCE
    code = 200 if answered or refused else 503 if status == ResearchStatus.SYSTEM_ERROR else 422
    citations: list[CitationPayload] = []
    if answered:
        evidence = {item.citation_id: item for item in result.result.evidence}
        for citation_id in result.result.citations:
            item = evidence.get(citation_id)
            if item is None:
                # Defense in depth: never publish an answer with an orphan citation.
                return ServingResult(422, _failure_payload(request_id, "CITATION_INVALID"))
            citations.append(
                {
                    "id": item.citation_id,
                    "document_id": item.document_id,
                    "chunk_id": item.chunk_id,
                    "source_url": _safe_public_source_url(item.source_url),
                }
            )
    return ServingResult(
        code,
        {
            "schema_version": 1,
            "request_id": request_id,
            "status": status.value,
            "answer": result.result.answer if answered or refused else "",
            "reason_code": result.reason,
            "citations": citations,
            "claim_support": "NOT_VERIFIED",
        },
    )


def _failure_payload(request_id: str, reason: str) -> ResearchPayload:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "status": ResearchStatus.VERIFICATION_FAILED.value,
        "answer": "",
        "reason_code": reason,
        "citations": [],
        "claim_support": "NOT_VERIFIED",
    }


class ResearchServingService:
    """Framework-neutral synchronous service: one validated question, one Graph call."""

    def __init__(self, workflow: ServingWorkflow) -> None:
        self.workflow = workflow

    def analyze(self, question: object, *, request_id: object) -> ServingResult:
        clean_question = validate_question(question)
        clean_id = validate_request_id(request_id)
        try:
            return present_result(self.workflow.invoke(clean_question), request_id=clean_id)
        except Exception:
            # Fail closed at presentation boundary. No exception text is exposed.
            return ServingResult(
                503,
                {
                    "schema_version": 1,
                    "request_id": clean_id,
                    "status": ResearchStatus.SYSTEM_ERROR.value,
                    "answer": "",
                    "reason_code": "SERVING_ERROR",
                    "citations": [],
                    "claim_support": "NOT_VERIFIED",
                },
            )
