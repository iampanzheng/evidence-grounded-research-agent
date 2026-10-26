"""Validated source catalog and immutable snapshot lock contracts (S1.1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"[a-z][a-z0-9_]*\Z")
_ALLOWED_STATUSES = {"candidate", "catalog_only"}


class RegistryValidationError(ValueError):
    """An untrusted registry or lock violates the declared S1.1 contract."""


@dataclass(frozen=True, slots=True)
class LockedDocument:
    path: str
    sha256: str
    size_bytes: int
    language: str
    source_url: str
    license_review: str
    public_distribution_approved: bool


@dataclass(frozen=True, slots=True)
class LockedSnapshot:
    snapshot_id: str
    source_id: str
    repository: str
    commit_sha: str
    upstream_verification: str
    documents: tuple[LockedDocument, ...]


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    repository: str
    allowed_roots: tuple[str, ...]
    languages: tuple[str, ...]
    status: str


def _dict(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryValidationError(f"{where}: expected object")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryValidationError(f"{where}: non-empty string required")
    return value


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise RegistryValidationError(f"{where}: expected array")
    return value


def _repository(value: Any) -> str:
    repo = _str(value, "repository")
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RegistryValidationError("repository: unsupported/untrusted URL")
    return repo


def _safe_path(path: str) -> str:
    if "\\" in path or "%" in path or any(ord(c) < 32 for c in path):
        raise RegistryValidationError("path contains forbidden characters")
    pure = PurePosixPath(path)
    if (
        not path.endswith(".md")
        or path.startswith("/")
        or path.startswith("./")
        or "//" in path
        or any(part in {".", ".."} for part in path.split("/"))
        or pure.is_absolute()
    ):
        raise RegistryValidationError(f"invalid document path: {path}")
    return path


def _schema(obj: dict[str, Any]) -> None:
    if type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
        raise RegistryValidationError("unsupported schema_version")


def parse_registry(raw: dict[str, Any]) -> dict[str, SourceSpec]:
    obj = _dict(raw, "registry")
    _schema(obj)
    result: dict[str, SourceSpec] = {}
    for item in _array(obj.get("sources"), "sources"):
        source = _dict(item, "source")
        sid = _str(source.get("source_id"), "source_id")
        if not _SOURCE.fullmatch(sid) or sid in result:
            raise RegistryValidationError("duplicate or invalid source_id")
        repo = _repository(source.get("repository"))
        roots = tuple(
            _str(p, "allowed_root") for p in _array(source.get("allowed_roots"), "allowed_roots")
        )
        if any(not p.endswith("/") or p.startswith("/") or ".." in p.split("/") for p in roots):
            raise RegistryValidationError("unsafe allowed_root")
        langs = tuple(_str(x, "language") for x in _array(source.get("languages"), "languages"))
        if len(set(langs)) != len(langs) or not langs:
            raise RegistryValidationError("duplicate or missing language")
        status = _str(source.get("status"), "status")
        if status not in _ALLOWED_STATUSES:
            raise RegistryValidationError("invalid source status")
        result[sid] = SourceSpec(sid, repo, roots, langs, status)
    if not result:
        raise RegistryValidationError("empty source registry")
    return result


def parse_lock(raw: dict[str, Any], sources: dict[str, SourceSpec]) -> dict[str, LockedSnapshot]:
    obj = _dict(raw, "lock")
    _schema(obj)
    result: dict[str, LockedSnapshot] = {}
    for item in _array(obj.get("snapshots"), "snapshots"):
        s = _dict(item, "snapshot")
        sid = _str(s.get("snapshot_id"), "snapshot_id")
        source_id = _str(s.get("source_id"), "source_id")
        if sid in result or source_id not in sources:
            raise RegistryValidationError("duplicate snapshot or unknown source")
        source = sources[source_id]
        repo = _repository(s.get("repository"))
        sha = _str(s.get("commit_sha"), "commit_sha")
        if repo != source.repository or not _HEX40.fullmatch(sha):
            raise RegistryValidationError("snapshot repository/commit mismatch")
        upstream_status = _str(s.get("upstream_verification"), "upstream_verification")
        if upstream_status not in {"pending", "verified"}:
            raise RegistryValidationError("invalid upstream verification")
        documents: list[LockedDocument] = []
        paths: set[str] = set()
        for entry in _array(s.get("documents"), "documents"):
            d = _dict(entry, "document")
            path = _safe_path(_str(d.get("path"), "path"))
            if path in paths or not any(path.startswith(root) for root in source.allowed_roots):
                raise RegistryValidationError("duplicate or out-of-scope document path")
            paths.add(path)
            digest = _str(d.get("sha256"), "sha256")
            if not _HEX64.fullmatch(digest):
                raise RegistryValidationError("invalid SHA-256 digest")
            size = d.get("bytes")
            if type(size) is not int or size < 0:
                raise RegistryValidationError("invalid document byte size")
            lang = _str(d.get("language"), "language")
            if lang not in source.languages:
                raise RegistryValidationError("document language outside source scope")
            url = _str(d.get("source_url"), "source_url")
            if url != f"{repo}/blob/{sha}/{quote(path, safe='/')}":
                raise RegistryValidationError("source URL does not match locked commit/path")
            review = _str(d.get("license_review"), "license_review")
            if review not in {"pending_file_level_review", "approved", "rejected"}:
                raise RegistryValidationError("unknown license review state")
            public = d.get("public_distribution_approved")
            if type(public) is not bool or (public and review != "approved"):
                raise RegistryValidationError("public distribution without approved license review")
            documents.append(LockedDocument(path, digest, size, lang, url, review, public))
        if not documents:
            raise RegistryValidationError("empty locked snapshot")
        result[sid] = LockedSnapshot(sid, source_id, repo, sha, upstream_status, tuple(documents))
    profiles = _dict(obj.get("profiles"), "profiles")
    for name, value in profiles.items():
        p = _dict(value, f"profile {name}")
        refs = _array(p.get("snapshot_ids"), "snapshot_ids")
        languages = _array(p.get("languages"), "languages")
        if not refs or not languages or any(x not in result for x in refs):
            raise RegistryValidationError("invalid profile snapshot reference")
        if len(set(refs)) != len(refs) or len(set(languages)) != len(languages):
            raise RegistryValidationError("duplicate profile entries")
        if any(
            not any(lang == doc.language for ref in refs for doc in result[ref].documents)
            for lang in languages
        ):
            raise RegistryValidationError("profile language not present in snapshot")
    if not result:
        raise RegistryValidationError("empty lock")
    return result


def load_source_contracts(
    directory: Path,
) -> tuple[dict[str, SourceSpec], dict[str, LockedSnapshot]]:
    """Read catalog and lock; never perform networking, mutation or automatic ref resolution."""
    try:
        registry = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
        lock = json.loads((directory / "snapshots.lock.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryValidationError(f"cannot read source contracts: {exc}") from exc
    sources = parse_registry(registry)
    return sources, parse_lock(lock, sources)
