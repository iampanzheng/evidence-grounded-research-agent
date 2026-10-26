"""Byte-exact deterministic Markdown segmentation of verified locked documents.

S1.4 treats the original UTF-8 bytes as canonical evidence. Structural headings
are detected outside fenced code blocks; no normalization changes original bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .acquisition import AcquisitionError, acquire_document
from .source_contracts import LockedDocument, LockedSnapshot

_HEADING = re.compile(rb"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*\r?\n?$", re.M)
_FENCE = re.compile(rb"^ {0,3}(`{3,}|~{3,})")


class ChunkingError(ValueError):
    """Input cannot be represented with lossless UTF-8 spans."""


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    chunk_id: str
    document_id: str
    snapshot_id: str
    source_id: str
    source_path: str
    source_url: str
    source_sha256: str
    language: str
    start_byte: int
    end_byte: int
    heading_path: tuple[str, ...]
    text: str


def _document_id(snapshot: LockedSnapshot, doc: LockedDocument) -> str:
    identity = json.dumps(
        [snapshot.source_id, snapshot.commit_sha, doc.path, doc.sha256],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "doc_" + hashlib.sha256(identity).hexdigest()


def _line_spans(data: bytes) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in data.splitlines(keepends=True):
        end = offset + len(line)
        spans.append((offset, end))
        offset = end
    if offset != len(data):
        raise ChunkingError("line span calculation failed")
    return spans


def _structural_sections(data: bytes) -> list[tuple[int, int, tuple[str, ...]]]:
    """Full byte coverage with heading ancestry, ignoring headings inside fences."""
    spans = _line_spans(data)
    if not spans:
        return []
    sections: list[tuple[int, int, tuple[str, ...]]] = []
    headings: list[tuple[int, str]] = []
    start = 0
    fence_char: int | None = None
    fence_width = 0
    for begin, end in spans:
        line = data[begin:end].rstrip(b"\r\n")
        fence = _FENCE.match(line)
        if fence:
            run = fence.group(1)
            if fence_char is None:
                fence_char, fence_width = run[0], len(run)
            elif (
                run[0] == fence_char and len(run) >= fence_width and not line[fence.end() :].strip()
            ):
                fence_char = None
            continue
        if fence_char is not None:
            continue
        match = _HEADING.match(line)
        if match:
            if begin > start:
                sections.append((start, begin, tuple(name for _, name in headings)))
            level = len(match.group(1))
            try:
                heading = match.group(2).decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise ChunkingError("invalid heading encoding") from exc
            headings = [(depth, name) for depth, name in headings if depth < level]
            headings.append((level, heading))
            start = begin
    if start < len(data):
        sections.append((start, len(data), tuple(name for _, name in headings)))
    return _merge_heading_only_sections(data, sections)


def _merge_heading_only_sections(
    data: bytes, sections: list[tuple[int, int, tuple[str, ...]]]
) -> list[tuple[int, int, tuple[str, ...]]]:
    """Avoid orphan title-only chunks while keeping complete source-byte coverage.

    A heading-only section can be combined with its following descendant or sibling
    when possible; the merged chunk carries the later (more specific) heading path.
    """
    merged: list[tuple[int, int, tuple[str, ...]]] = []
    index = 0
    while index < len(sections):
        begin, end, heading = sections[index]
        while index + 1 < len(sections):
            text = data[begin:end].decode("utf-8")
            # Pure ATX title followed by whitespace: no standalone answer material.
            if not re.fullmatch(r"(?s)\s*#{1,6}[ \t]+[^\n]+\s*", text):
                break
            next_begin, next_end, next_heading = sections[index + 1]
            if end != next_begin:
                break
            end, heading = next_end, next_heading
            index += 1
        merged.append((begin, end, heading))
        index += 1
    return merged


def _fence_safe_breaks(data: bytes, begin: int, end: int) -> set[int]:
    """Return line-end offsets outside fenced blocks, including closing lines."""
    safe: set[int] = set()
    opened: tuple[int, int] | None = None
    for start, stop in _line_spans(data[begin:end]):
        line = data[begin + start : begin + stop].rstrip(b"\r\n")
        match = _FENCE.match(line)
        if match:
            run = match.group(1)
            if opened is None:
                opened = (run[0], len(run))
            elif run[0] == opened[0] and len(run) >= opened[1] and not line[match.end() :].strip():
                opened = None
        if opened is None:
            safe.add(begin + stop)
    return safe


def _split_section(data: bytes, begin: int, end: int, limit: int) -> list[tuple[int, int]]:
    """Prefer line boundaries, then split oversized lines at UTF-8 codepoint boundaries."""
    result: list[tuple[int, int]] = []
    cursor = begin
    safe = _fence_safe_breaks(data, begin, end)
    while cursor < end:
        ceiling = min(cursor + limit, end)
        if ceiling == end:
            result.append((cursor, end))
            break
        candidates = [point for point in safe if cursor < point <= ceiling]
        if candidates:
            split = max(candidates)
        else:
            # An oversized fenced block is unavoidable: use a line boundary
            # and preserve continuation as raw source, without fake backticks.
            split = data.rfind(b"\n", cursor, ceiling)
            if split >= cursor:
                split += 1
            else:
                split = ceiling
                while split > cursor and split < len(data) and (data[split] & 0xC0) == 0x80:
                    split -= 1
                if split == cursor:
                    raise ChunkingError("chunk byte limit cannot fit one UTF-8 codepoint")
        if split == cursor:
            raise ChunkingError("chunker failed to advance")
        result.append((cursor, split))
        cursor = split
    return result


def chunk_markdown(
    snapshot: LockedSnapshot,
    doc: LockedDocument,
    content: bytes,
    *,
    max_chunk_bytes: int = 2048,
) -> tuple[MarkdownChunk, ...]:
    """Return reproducible, contiguous and reversible chunks of canonical source bytes."""
    if doc not in snapshot.documents:
        raise ChunkingError("document not present in locked snapshot")
    if max_chunk_bytes < 4:
        raise ChunkingError("max_chunk_bytes must be >= 4")
    if hashlib.sha256(content).hexdigest() != doc.sha256 or len(content) != doc.size_bytes:
        raise ChunkingError("input differs from locked source")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ChunkingError("input is not UTF-8") from exc
    doc_id = _document_id(snapshot, doc)
    chunks: list[MarkdownChunk] = []
    for begin, end, headings in _structural_sections(content):
        for start, stop in _split_section(content, begin, end, max_chunk_bytes):
            raw = content[start:stop]
            try:
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ChunkingError("invalid byte boundary") from exc
            chunk_id = (
                "chunk_" + hashlib.sha256(f"{doc_id}:{start}:{stop}".encode("ascii")).hexdigest()
            )
            chunks.append(
                MarkdownChunk(
                    chunk_id,
                    doc_id,
                    snapshot.snapshot_id,
                    snapshot.source_id,
                    doc.path,
                    doc.source_url,
                    doc.sha256,
                    doc.language,
                    start,
                    stop,
                    headings,
                    text,
                )
            )
    validate_chunks(content, tuple(chunks))
    if any(
        chunk.document_id != doc_id
        or chunk.snapshot_id != snapshot.snapshot_id
        or chunk.source_id != snapshot.source_id
        or chunk.source_path != doc.path
        or chunk.source_sha256 != doc.sha256
        or chunk.source_url != doc.source_url
        or chunk.language != doc.language
        for chunk in chunks
    ):
        raise ChunkingError("chunk provenance mismatches lock")
    return tuple(chunks)


def validate_chunks(content: bytes, chunks: tuple[MarkdownChunk, ...]) -> None:
    """Reject gaps, overlaps, tampered spans, duplicate IDs and non-roundtripping data."""
    if not chunks and content:
        raise ChunkingError("nonempty document has no chunks")
    position = 0
    ids: set[str] = set()
    for chunk in chunks:
        if chunk.start_byte != position or chunk.end_byte <= position:
            raise ChunkingError("gap, overlap or empty chunk")
        expected_id = (
            "chunk_"
            + hashlib.sha256(
                f"{chunk.document_id}:{chunk.start_byte}:{chunk.end_byte}".encode("ascii")
            ).hexdigest()
        )
        if chunk.chunk_id != expected_id:
            raise ChunkingError("chunk identifier is not bound to its span")
        if chunk.chunk_id in ids:
            raise ChunkingError("duplicate chunk identifier")
        encoded = chunk.text.encode("utf-8")
        if content[chunk.start_byte : chunk.end_byte] != encoded:
            raise ChunkingError("chunk text does not match source bytes")
        ids.add(chunk.chunk_id)
        position = chunk.end_byte
    if position != len(content):
        raise ChunkingError("chunk sequence does not cover document")


def chunk_cached_snapshot(
    snapshot: LockedSnapshot, cache_root: Path, *, max_chunk_bytes: int = 2048
) -> tuple[MarkdownChunk, ...]:
    """Only process already verified local cache; never fetch silently."""
    result: list[MarkdownChunk] = []
    for doc in snapshot.documents:
        try:
            acquired = acquire_document(snapshot, doc, cache_root, offline=True)
        except AcquisitionError as exc:
            raise ChunkingError(f"verified cache unavailable: {doc.path}: {exc}") from exc
        result.extend(
            chunk_markdown(
                snapshot, doc, acquired.cached_path.read_bytes(), max_chunk_bytes=max_chunk_bytes
            )
        )
    return tuple(result)


def chunks_manifest(chunks: tuple[MarkdownChunk, ...]) -> bytes:
    """Portable canonical JSON; excludes machine-local paths and cache contents."""
    return (
        json.dumps(
            {"schema_version": 1, "chunks": [asdict(chunk) for chunk in chunks]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
