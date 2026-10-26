"""Only stable minimal domain vocabulary; Stage 1+ owns actual domain contract implementation."""

from dataclasses import dataclass
from enum import StrEnum


class ResearchStatus(StrEnum):
    ANSWERED = "ANSWERED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    snapshot_id: str
    document_id: str
    source_sha256: str
