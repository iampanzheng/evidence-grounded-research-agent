"""Optional multilingual dense search and rank-based hybrid fusion.

The signed corpus stays immutable. No embeddings or document text are exported.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from p2_research.application.retrieval.bm25 import BM25Index, Hit, RetrievalError

if TYPE_CHECKING:
    from p2_research.application.retrieval.passage import PassageDenseIndex


class Embedder(Protocol):
    """Embeds texts in a shared multilingual vector space."""

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Lazy optional dependency; downloading a model requires explicit local setup."""

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RetrievalError(
                "semantic extra missing: install with `uv sync --extra semantic`"
            ) from exc
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        return vectors.tolist()  # type: ignore[no-any-return]


def _normalized(vector: Sequence[float], dimension: int | None = None) -> tuple[float, ...]:
    if not vector or (dimension is not None and len(vector) != dimension):
        raise RetrievalError("invalid embedding dimension")
    try:
        values = tuple(float(x) for x in vector)
    except (ValueError, TypeError) as exc:
        raise RetrievalError("invalid embedding values") from exc
    if any(not math.isfinite(x) for x in values):
        raise RetrievalError("non-finite embedding")
    norm = math.sqrt(sum(x * x for x in values))
    if norm <= 0 or not math.isfinite(norm):
        raise RetrievalError("zero embedding")
    return tuple(x / norm for x in values)


class DenseIndex:
    """Deterministic exact cosine search over locally embedded frozen chunks."""

    def __init__(self, chunks: list[dict[str, Any]], embedder: Embedder, *, batch_size: int = 32):
        if not chunks or batch_size < 1:
            raise RetrievalError("invalid dense index configuration")
        self.chunks = chunks
        self.embedder = embedder
        ids = [str(c["chunk_id"]) for c in chunks]
        if len(set(ids)) != len(ids):
            raise RetrievalError("duplicate chunk identities")
        vectors: list[tuple[float, ...]] = []
        dimension: int | None = None
        for start in range(0, len(chunks), batch_size):
            batch = [str(c["text"]) for c in chunks[start : start + batch_size]]
            encoded = embedder.encode(batch)
            if len(encoded) != len(batch):
                raise RetrievalError("embedding batch size mismatch")
            for vector in encoded:
                normalized = _normalized(vector, dimension)
                dimension = len(normalized)
                vectors.append(normalized)
        self.dimension = dimension
        self.vectors = vectors

    def search(self, question: str, k: int = 5) -> list[Hit]:
        if not 1 <= k <= 100:
            raise RetrievalError("k must be between 1 and 100")
        if not question.strip():
            return []
        query_batch = self.embedder.encode([question])
        if len(query_batch) != 1:
            raise RetrievalError("invalid query embedding batch")
        query = _normalized(query_batch[0], self.dimension)
        scored = [
            (sum(x * y for x, y in zip(query, vector, strict=True)), i)
            for i, vector in enumerate(self.vectors)
        ]
        scored.sort(key=lambda pair: (-pair[0], self.chunks[pair[1]]["chunk_id"]))
        return [
            Hit(
                rank,
                c["chunk_id"],
                c["document_id"],
                c["source_path"],
                c["source_url"],
                round(score, 8),
            )
            for rank, (score, i) in enumerate(scored[:k], start=1)
            for c in [self.chunks[i]]
        ]


class HybridIndex:
    """Reciprocal Rank Fusion; no cross-model score calibration needed."""

    def __init__(
        self, lexical: BM25Index, dense: DenseIndex | PassageDenseIndex, *, rrf_k: int = 60
    ):
        if rrf_k < 1:
            raise RetrievalError("rrf_k must be positive")
        if [c["chunk_id"] for c in lexical.chunks] != [c["chunk_id"] for c in dense.chunks]:
            raise RetrievalError("hybrid index corpus mismatch")
        self.lexical, self.dense, self.rrf_k = lexical, dense, rrf_k

    def search(self, question: str, k: int = 5) -> list[Hit]:
        if not 1 <= k <= 100:
            raise RetrievalError("k must be between 1 and 100")
        scored: dict[str, float] = {}
        metadata: dict[str, Hit] = {}
        for retriever in (self.lexical, self.dense):
            for hit in retriever.search(question, 100):
                scored[hit.chunk_id] = scored.get(hit.chunk_id, 0.0) + 1 / (self.rrf_k + hit.rank)
                metadata[hit.chunk_id] = hit
        ids = sorted(scored, key=lambda cid: (-scored[cid], cid))[:k]
        return [
            Hit(
                rank,
                cid,
                metadata[cid].document_id,
                metadata[cid].source_path,
                metadata[cid].source_url,
                round(scored[cid], 8),
            )
            for rank, cid in enumerate(ids, start=1)
        ]
