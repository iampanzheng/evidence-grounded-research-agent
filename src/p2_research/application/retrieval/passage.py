"""Token-aware embedding passages mapped back to immutable frozen chunk IDs.

Passages are transient index inputs, never changes to the frozen corpus or Gold.
"""

from __future__ import annotations

import math
from typing import Any

from p2_research.application.retrieval.bm25 import Hit, RetrievalError
from p2_research.application.retrieval.semantic import Embedder, _normalized


def token_passages(
    text: str, tokenizer: Any, *, max_sequence_length: int, overlap: int = 16
) -> list[str]:
    """Split on tokenizer offsets so encoded passages fit a model's token budget."""
    if not text.strip() or max_sequence_length < 3 or overlap < 0:
        raise RetrievalError("invalid passage configuration")
    specials = tokenizer.num_special_tokens_to_add(pair=False)
    budget = max_sequence_length - specials
    if budget < 1 or overlap >= budget:
        raise RetrievalError("overlap must be smaller than available content token budget")
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    offsets = encoded.get("offset_mapping")
    if not isinstance(offsets, list) or not offsets:
        raise RetrievalError("fast tokenizer offset mapping required")
    if any(
        not isinstance(item, (tuple, list))
        or len(item) != 2
        or not isinstance(item[0], int)
        or not isinstance(item[1], int)
        or item[0] < 0
        or item[1] > len(text)
        or item[1] <= item[0]
        for item in offsets
    ):
        raise RetrievalError("invalid tokenizer offsets")
    passages: list[str] = []
    start = 0
    while start < len(offsets):
        end = min(start + budget, len(offsets))
        # Offset-based text windows can expand when the tokenizer sees a new
        # word boundary. Shrink until the actual re-encoding fits.
        while end > start:
            window = offsets[start:end]
            passage = text[window[0][0] : window[-1][1]]
            if (
                passage.strip()
                and len(tokenizer(passage, add_special_tokens=True, truncation=False)["input_ids"])
                <= max_sequence_length
            ):
                break
            end -= 1
        if end <= start:
            raise RetrievalError("cannot produce a valid bounded passage")
        passages.append(passage)
        if end >= len(offsets):
            break
        start = max(start + 1, end - overlap)
    return passages


class PassageDenseIndex:
    """Exact cosine over passages, max-score aggregated to parent frozen chunks."""

    def __init__(
        self,
        chunks: list[dict[str, Any]],
        embedder: Embedder,
        *,
        tokenizer: Any,
        max_sequence_length: int,
        overlap: int = 16,
        batch_size: int = 32,
    ) -> None:
        if len(chunks) != 409 or batch_size < 1:
            raise RetrievalError("frozen 409-chunk corpus and positive batch required")
        self.chunks = chunks
        self.embedder = embedder
        ids = [str(c["chunk_id"]) for c in chunks]
        if len(set(ids)) != len(ids):
            raise RetrievalError("duplicate chunk IDs")
        passages: list[str] = []
        self.parent_indices: list[int] = []
        for index, chunk in enumerate(chunks):
            pieces = token_passages(
                str(chunk["text"]),
                tokenizer,
                max_sequence_length=max_sequence_length,
                overlap=overlap,
            )
            passages.extend(pieces)
            self.parent_indices.extend([index] * len(pieces))
        self.passage_count = len(passages)
        self.vectors: list[tuple[float, ...]] = []
        dimension: int | None = None
        for start in range(0, len(passages), batch_size):
            batch = passages[start : start + batch_size]
            encoded = embedder.encode(batch)
            if len(encoded) != len(batch):
                raise RetrievalError("embedding batch size mismatch")
            for vector in encoded:
                normalized = _normalized(vector, dimension)
                dimension = len(normalized)
                self.vectors.append(normalized)
        self.dimension = dimension

    def search(self, question: str, k: int = 5) -> list[Hit]:
        if not 1 <= k <= 100:
            raise RetrievalError("k must be between 1 and 100")
        if not question.strip():
            return []
        encoded = self.embedder.encode([question])
        if len(encoded) != 1:
            raise RetrievalError("invalid query embedding batch")
        query = _normalized(encoded[0], self.dimension)
        best: dict[int, float] = {}
        for parent, vector in zip(self.parent_indices, self.vectors, strict=True):
            score = math.fsum(x * y for x, y in zip(query, vector, strict=True))
            best[parent] = max(best.get(parent, -math.inf), score)
        ranked = sorted(best.items(), key=lambda item: (-item[1], self.chunks[item[0]]["chunk_id"]))
        return [
            Hit(
                rank,
                c["chunk_id"],
                c["document_id"],
                c["source_path"],
                c["source_url"],
                round(score, 8),
            )
            for rank, (index, score) in enumerate(ranked[:k], start=1)
            for c in [self.chunks[index]]
        ]
