"""Pinned GitHub document acquisition with verified, content-addressed cache.

No implicit HEAD resolution and no permission to publish fetched documents.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from .source_contracts import LockedDocument, LockedSnapshot

_JSON_LIMIT = 12 * 1024 * 1024
_URL_PREFIX = "https://api.github.com/repos/"
_GIT_BLOB = re.compile(r"[0-9a-f]{40}\Z")


class AcquisitionError(RuntimeError):
    """A pinned document cannot be obtained or verified safely."""


@dataclass(frozen=True, slots=True)
class AcquiredDocument:
    source_path: str
    sha256: str
    cached_path: Path
    git_blob_sha: str
    from_cache: bool


# Injectable transport keeps tests independent from the internet.
Transport = Callable[[str], bytes]


def github_api_get(url: str) -> bytes:
    """Read only from the exact API origin; urllib redirects are not followed here."""
    if not url.startswith(_URL_PREFIX):
        raise AcquisitionError("unsupported API origin")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "p2-corpus/0.1"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        # Refuse HTTP redirects, whose destination may be untrusted.
        from urllib.request import HTTPRedirectHandler, build_opener

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                return None

        with build_opener(NoRedirect).open(request, timeout=30) as response:
            if response.status != 200:
                raise AcquisitionError(f"upstream returned HTTP {response.status}")
            body = response.read(_JSON_LIMIT + 1)
            if not isinstance(body, bytes):
                raise AcquisitionError("unexpected HTTP response type")
            data: bytes = body
    except HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining", "")
        if exc.code in {403, 429} and (remaining == "0" or "rate limit" in exc.reason.lower()):
            raise AcquisitionError("UPSTREAM_RATE_LIMITED: GitHub API quota exhausted") from exc
        raise AcquisitionError(f"upstream HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AcquisitionError(f"upstream request failed: {type(exc).__name__}") from exc
    if len(data) > _JSON_LIMIT:
        raise AcquisitionError("upstream response too large")
    return data


def _blob_sha(content: bytes) -> str:
    payload = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    return hashlib.sha1(payload).hexdigest()  # noqa: S324 - Git protocol blob ID, not security hash


def _validated_payload(payload: bytes, doc: LockedDocument) -> tuple[bytes, str]:
    try:
        obj = json.loads(payload)
        if not isinstance(obj, dict) or obj.get("type") != "file":
            raise AcquisitionError("upstream object is not a file")
        if obj.get("path") != doc.path or obj.get("encoding") != "base64":
            raise AcquisitionError("upstream path or encoding mismatch")
        sha = obj.get("sha")
        if not isinstance(sha, str) or not _GIT_BLOB.fullmatch(sha):
            raise AcquisitionError("invalid upstream Git blob SHA")
        content_string = obj.get("content")
        if not isinstance(content_string, str):
            raise AcquisitionError("missing upstream content")
        content = base64.b64decode("".join(content_string.split()), validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise AcquisitionError("malformed upstream document") from exc
    if len(content) != doc.size_bytes:
        raise AcquisitionError("upstream size mismatch")
    if hashlib.sha256(content).hexdigest() != doc.sha256:
        raise AcquisitionError("upstream SHA-256 mismatch")
    if _blob_sha(content) != sha:
        raise AcquisitionError("upstream Git blob SHA mismatch")
    return content, sha


def _paths(cache_root: Path, snapshot: LockedSnapshot, doc: LockedDocument) -> tuple[Path, Path]:
    # A flat content-addressed layout avoids interpreting remote paths as filesystem paths.
    key = hashlib.sha256(
        f"{snapshot.repository}:{snapshot.commit_sha}:{doc.path}".encode()
    ).hexdigest()
    return cache_root / "objects" / doc.sha256, cache_root / "receipts" / f"{key}.json"


def _read_cached(
    cache_root: Path, snapshot: LockedSnapshot, doc: LockedDocument
) -> tuple[Path, str] | None:
    obj, receipt = _paths(cache_root, snapshot, doc)
    if not receipt.exists() and not receipt.is_symlink():
        return None
    if obj.is_symlink() or receipt.is_symlink() or not obj.is_file() or not receipt.is_file():
        raise AcquisitionError("cache is incomplete or contains symlinks")
    try:
        data = obj.read_bytes()
        record = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AcquisitionError("unreadable cache object or receipt") from exc
    if len(data) != doc.size_bytes or hashlib.sha256(data).hexdigest() != doc.sha256:
        raise AcquisitionError("cached document checksum/size mismatch")
    if (
        not isinstance(record, dict)
        or record.get("sha256") != doc.sha256
        or record.get("path") != doc.path
        or record.get("commit_sha") != snapshot.commit_sha
    ):
        raise AcquisitionError("cached receipt does not match lock")
    blob = record.get("git_blob_sha")
    if not isinstance(blob, str) or not _GIT_BLOB.fullmatch(blob) or _blob_sha(data) != blob:
        raise AcquisitionError("cached Git blob checksum mismatch")
    return obj, blob


def _atomic_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise AcquisitionError("refusing symlink target")
    fd, name = tempfile.mkstemp(prefix=".p2-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Existing files may be valid in a concurrent run; they are verified on next read.
        if not path.exists():
            os.replace(name, path)
        else:
            os.unlink(name)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def acquire_document(
    snapshot: LockedSnapshot,
    doc: LockedDocument,
    cache_root: Path,
    *,
    offline: bool = False,
    transport: Transport = github_api_get,
) -> AcquiredDocument:
    """Acquire one locked Markdown file via GitHub API and verify two independent hashes.

    No HEAD lookup, file-system paths from upstream, or silent cache repair.
    """
    if doc not in snapshot.documents:
        raise AcquisitionError("document is not included in pinned snapshot")
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", snapshot.repository
    ):
        raise AcquisitionError("unsupported repository")
    if not _GIT_BLOB.fullmatch(snapshot.commit_sha):
        raise AcquisitionError("invalid pinned commit")
    cached = _read_cached(cache_root, snapshot, doc)
    if cached is not None:
        return AcquiredDocument(doc.path, doc.sha256, cached[0], cached[1], True)
    if offline:
        raise AcquisitionError(f"OFFLINE_CACHE_MISS: {doc.sha256}")
    owner_repo = snapshot.repository.removeprefix("https://github.com/")
    api_url = (
        f"{_URL_PREFIX}{owner_repo}/contents/{quote(doc.path, safe='/')}?ref={snapshot.commit_sha}"
    )
    content, blob = _validated_payload(transport(api_url), doc)
    obj, receipt = _paths(cache_root, snapshot, doc)
    _atomic_new(obj, content)
    _atomic_new(
        receipt,
        (
            json.dumps(
                {
                    "sha256": doc.sha256,
                    "git_blob_sha": blob,
                    "path": doc.path,
                    "commit_sha": snapshot.commit_sha,
                },
                sort_keys=True,
            )
            + "\n"
        ).encode(),
    )
    _read_cached(cache_root, snapshot, doc)
    return AcquiredDocument(doc.path, doc.sha256, obj, blob, False)


def acquire_snapshot(
    snapshot: LockedSnapshot,
    cache_root: Path,
    *,
    offline: bool = False,
    transport: Transport = github_api_get,
) -> tuple[AcquiredDocument, ...]:
    """Cache all locked documents; fail visibly if any document is unavailable."""
    return tuple(
        acquire_document(snapshot, doc, cache_root, offline=offline, transport=transport)
        for doc in snapshot.documents
    )
