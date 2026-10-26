"""Deterministic, dependency-free BM25 baseline on locked, real-world chunks."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_CHUNKS_SHA256 = "a312e11036c2a7d6b723c9fbc5dfd8d984ec9865be7e41b00fdb9feaaa4d7fa1"
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*|[0-9]+|[\u3400-\u9fff]+")


class RetrievalError(ValueError):
    """Invalid corpus or retrieval request."""


def tokenize(text: str) -> list[str]:
    """Stable lexical tokenization: English words, Chinese unigrams and bigrams."""
    out: list[str] = []
    for match in TOKEN_RE.finditer(text.lower()):
        part = match.group()
        if "\u3400" <= part[0] <= "\u9fff":
            out.extend(part)
            out.extend(part[i : i + 2] for i in range(len(part) - 1))
        else:
            out.append(part)
    return out


@dataclass(frozen=True)
class Hit:
    rank: int
    chunk_id: str
    document_id: str
    source_path: str
    source_url: str
    score: float

    def as_dict(self) -> dict[str, str | int | float]:
        return {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source_path": self.source_path,
            "source_url": self.source_url,
            "score": self.score,
        }


class BM25Index:
    """Immutable in-memory sparse retrieval index; no text is written to reports."""

    def __init__(
        self,
        chunks: list[dict[str, Any]],
        *,
        k1: float = 1.2,
        b: float = 0.75,
        tokenizer: Callable[[str], list[str]] = tokenize,
    ):
        if not chunks or not (k1 > 0 and 0 <= b <= 1):
            raise RetrievalError("empty corpus or invalid BM25 parameters")
        ids = [c["chunk_id"] for c in chunks]
        if len(set(ids)) != len(ids):
            raise RetrievalError("duplicate chunk identities")
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tokenizer = tokenizer
        self.tfs = [Counter(self.tokenizer(c["text"])) for c in chunks]
        self.lengths = [sum(freq.values()) for freq in self.tfs]
        self.avgdl = sum(self.lengths) / len(chunks)
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(chunks)
        self.idf = {t: math.log1p((n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, question: str, k: int = 5) -> list[Hit]:
        if not 1 <= k <= 100:
            raise RetrievalError("k must be between 1 and 100")
        terms = set(self.tokenizer(question))
        if not terms:
            return []
        ranked: list[tuple[float, int]] = []
        for i, tf in enumerate(self.tfs):
            length_factor = self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avgdl)
            score = sum(
                self.idf.get(t, 0.0) * (tf[t] * (self.k1 + 1)) / (tf[t] + length_factor)
                for t in terms
                if tf[t]
            )
            if score > 0:
                ranked.append((score, i))
        ranked.sort(key=lambda pair: (-pair[0], self.chunks[pair[1]]["chunk_id"]))
        return [
            Hit(
                rank,
                c["chunk_id"],
                c["document_id"],
                c["source_path"],
                c["source_url"],
                round(score, 8),
            )
            for rank, (score, i) in enumerate(ranked[:k], start=1)
            for c in [self.chunks[i]]
        ]


def load_locked_chunks(path: Path) -> list[dict[str, Any]]:
    """Reject mismatched raw manifest before any indexing."""
    try:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != EXPECTED_CHUNKS_SHA256:
            raise RetrievalError("CHUNK_MANIFEST_DRIFT")
        obj = json.loads(data)
        chunks = obj["chunks"]
        if not isinstance(chunks, list) or len(chunks) != 409:
            raise RetrievalError("unexpected chunk count")
        return chunks
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, RetrievalError):
            raise
        raise RetrievalError("invalid or missing frozen manifest") from exc
