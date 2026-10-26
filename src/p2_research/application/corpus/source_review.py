"""Fail-closed source and license review records; never auto-approve rights."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .source_contracts import LockedSnapshot

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_VALID = {"pending", "verified", "rejected"}


class SourceReviewError(ValueError):
    """A source review record is missing, malformed, or disagrees with the lock."""


@dataclass(frozen=True, slots=True)
class FileReview:
    path: str
    document_sha256: str
    decision: str
    license_id: str | None
    evidence_url: str | None
    reviewer: str | None
    reviewed_at: str | None
    notes: str


def _optional_review_field(entry: dict[str, Any], name: str) -> str | None:
    """Validate an optional field without closing over a loop variable."""
    value = entry.get(name)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise SourceReviewError(f"invalid review {name}")
    return value


def load_file_reviews(path: Path, snapshot: LockedSnapshot) -> tuple[FileReview, ...]:
    """Require one record per locked document, with unchanged content identity.

    A decision of 'approved' must have a review trail; this function does NOT
    grant approval to any other publication subsystem.
    """
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SourceReviewError("invalid file review schema")
    if (
        data.get("snapshot_id") != snapshot.snapshot_id
        or data.get("commit_sha") != snapshot.commit_sha
    ):
        raise SourceReviewError("review is not bound to the pinned snapshot")
    records = data.get("files")
    if not isinstance(records, list) or len(records) != len(snapshot.documents):
        raise SourceReviewError("file reviews must cover exactly the locked documents")
    by_path = {doc.path: doc for doc in snapshot.documents}
    result: list[FileReview] = []
    seen: set[str] = set()
    for entry in records:
        if not isinstance(entry, dict):
            raise SourceReviewError("invalid file review entry")
        file_path = entry.get("path")
        if not isinstance(file_path, str) or file_path not in by_path or file_path in seen:
            raise SourceReviewError("unknown or duplicate file review path")
        seen.add(file_path)
        if entry.get("document_sha256") != by_path[file_path].sha256:
            raise SourceReviewError("file review content checksum mismatch")
        decision = entry.get("decision")
        if decision not in {"pending", "approved", "rejected"}:
            raise SourceReviewError("unsupported file review decision")
        license_id = _optional_review_field(entry, "license_id")
        evidence_url = _optional_review_field(entry, "evidence_url")
        reviewer = _optional_review_field(entry, "reviewer")
        reviewed_at = _optional_review_field(entry, "reviewed_at")
        if decision == "approved" and not all((license_id, evidence_url, reviewer, reviewed_at)):
            raise SourceReviewError("approval requires license, evidence, reviewer and timestamp")
        notes = entry.get("notes", "")
        if not isinstance(notes, str):
            raise SourceReviewError("invalid review notes")
        result.append(
            FileReview(
                file_path,
                by_path[file_path].sha256,
                decision,
                license_id,
                evidence_url,
                reviewer,
                reviewed_at,
                notes,
            )
        )
    return tuple(result)


def validate_commit_identity(snapshot: LockedSnapshot, payload: bytes) -> str:
    """Check a GitHub commit API response against an immutable snapshot identity.

    A matching API response is evidence, not independent cryptographic proof of
    repository ownership or a license approval.
    """
    if not _HEX40.fullmatch(snapshot.commit_sha):
        raise SourceReviewError("invalid commit SHA")
    try:
        obj: Any = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise SourceReviewError("invalid commit response") from exc
    if not isinstance(obj, dict) or obj.get("sha") != snapshot.commit_sha:
        raise SourceReviewError("upstream commit differs from snapshot lock")
    commit = obj.get("commit")
    if not isinstance(commit, dict) or not isinstance(commit.get("tree"), dict):
        raise SourceReviewError("missing commit tree")
    tree = commit["tree"].get("sha")
    if not isinstance(tree, str) or not _HEX40.fullmatch(tree):
        raise SourceReviewError("invalid commit tree SHA")
    return tree


def verify_upstream_commit(snapshot: LockedSnapshot) -> str:
    """Fetch the pinned commit's tree identity; never resolve a mutable ref."""
    from .acquisition import github_api_get

    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", snapshot.repository
    ):
        raise SourceReviewError("unsupported source repository")
    owner_repo = snapshot.repository.removeprefix("https://github.com/")
    url = f"https://api.github.com/repos/{owner_repo}/commits/{snapshot.commit_sha}"
    return validate_commit_identity(snapshot, github_api_get(url))
