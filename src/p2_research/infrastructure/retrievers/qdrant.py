"""Qdrant REST adapter for pinned, immutable dense indexes.

Only vectors, IDs and source metadata are stored, never chunk text.
A manifest sentinel is written last; incomplete namespaces fail closed.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from p2_research.application.retrieval.bm25 import Hit, RetrievalError
from p2_research.application.retrieval.semantic import Embedder
from p2_research.infrastructure.retrievers.pgvector import (
    EXPECTED_DIMENSION,
    corpus_digest,
    validate_vector,
)

_NAMESPACE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
_MANIFEST_ID = "00000000-0000-0000-0000-000000000001"


def _point_id(namespace: str, chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"p2-qdrant:{namespace}:{chunk_id}"))


class QdrantIndex:
    """Exact cosine search, reproducible per namespace and embedding model."""

    def __init__(
        self,
        *,
        url: str,
        namespace: str,
        model_id: str,
        embedder: Embedder,
        dimension: int = EXPECTED_DIMENSION,
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not _NAMESPACE_RE.fullmatch(namespace):
            raise RetrievalError("invalid Qdrant namespace")
        if not model_id.strip():
            raise RetrievalError("model_id is required")
        if dimension != EXPECTED_DIMENSION:
            raise RetrievalError("this index requires 384-dimensional embeddings")
        if not url.startswith(("http://", "https://")) or "?" in url or "#" in url:
            raise RetrievalError("invalid Qdrant HTTP URL")
        if timeout <= 0:
            raise RetrievalError("timeout must be positive")
        self.url = url.rstrip("/")
        self.namespace = namespace
        self.collection = f"p2_{namespace}"
        self.model_id = model_id
        self.embedder = embedder
        self.dimension = dimension
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        body = json.dumps(data, allow_nan=False).encode("utf-8") if data is not None else None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        request = Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result: Any = json.load(response)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise RetrievalError(f"Qdrant HTTP error: {exc.code}") from exc
        except (URLError, TimeoutError, ValueError) as exc:
            raise RetrievalError("Qdrant request failed") from exc
        if not isinstance(result, dict) or result.get("status") not in {"ok", "success"}:
            raise RetrievalError("unexpected Qdrant response")
        return result.get("result")

    def _path(self, suffix: str = "") -> str:
        return f"/collections/{quote(self.collection, safe='')}{suffix}"

    def initialize(self) -> None:
        existing = self._request("GET", self._path())
        if existing is None:
            self._request(
                "PUT",
                self._path(),
                {"vectors": {"size": self.dimension, "distance": "Cosine"}},
            )
            existing = self._request("GET", self._path())
        if not isinstance(existing, dict):
            raise RetrievalError("Qdrant collection could not be read")
        config = existing.get("config", {}).get("params", {}).get("vectors", {})
        if config.get("size") != self.dimension or config.get("distance") != "Cosine":
            raise RetrievalError("Qdrant collection vector configuration mismatch")

    def _manifest(self) -> dict[str, Any] | None:
        result = self._request(
            "POST",
            self._path("/points"),
            {"ids": [_MANIFEST_ID], "with_payload": True, "with_vector": False},
        )
        if not isinstance(result, list):
            raise RetrievalError("invalid Qdrant manifest lookup")
        if not result:
            return None
        payload: Any = result[0].get("payload")
        if not isinstance(payload, dict) or payload.get("kind") != "manifest":
            raise RetrievalError("invalid Qdrant manifest")
        return payload

    def _count(self) -> int:
        result = self._request(
            "POST",
            self._path("/points/count"),
            {"filter": {"must": [{"key": "kind", "match": {"value": "chunk"}}]}, "exact": True},
        )
        if not isinstance(result, dict) or not isinstance(result.get("count"), int):
            raise RetrievalError("invalid Qdrant count")
        return int(result["count"])

    def upsert_corpus(
        self, chunks: list[dict[str, Any]], *, batch_size: int = 32
    ) -> dict[str, Any]:
        if batch_size < 1:
            raise RetrievalError("batch_size must be positive")
        digest = corpus_digest(chunks)
        self.initialize()
        expected = {
            "kind": "manifest",
            "digest": digest,
            "model_id": self.model_id,
            "dimension": self.dimension,
            "chunks": len(chunks),
        }
        manifest = self._manifest()
        count = self._count()
        if manifest is not None:
            if manifest != expected or count != len(chunks):
                raise RetrievalError("namespace manifest mismatch or incomplete index")
            return {"status": "ALREADY_INDEXED", "chunks": count, "digest": digest}
        if count:
            raise RetrievalError("incomplete Qdrant index: refuse partial overwrite")

        # The final manifest is the commit marker. Interrupted uploads are detectable.
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = self.embedder.encode([str(c["text"]) for c in batch])
            if len(vectors) != len(batch):
                raise RetrievalError("embedding batch size mismatch")
            points = []
            for chunk, vector in zip(batch, vectors, strict=True):
                points.append(
                    {
                        "id": _point_id(self.namespace, str(chunk["chunk_id"])),
                        "vector": validate_vector(vector, self.dimension),
                        "payload": {
                            "kind": "chunk",
                            "chunk_id": str(chunk["chunk_id"]),
                            "document_id": str(chunk["document_id"]),
                            "source_path": str(chunk["source_path"]),
                            "source_url": str(chunk["source_url"]),
                        },
                    }
                )
            self._request("PUT", self._path("/points?wait=true"), {"points": points})
        if self._count() != len(chunks):
            raise RetrievalError("Qdrant indexed chunk count mismatch")
        self._request(
            "PUT",
            self._path("/points?wait=true"),
            {
                "points": [
                    {
                        "id": _MANIFEST_ID,
                        "vector": [1.0] + [0.0] * (self.dimension - 1),
                        "payload": expected,
                    }
                ],
            },
        )
        if self._manifest() != expected:
            raise RetrievalError("Qdrant manifest commit failed")
        return {"status": "INDEXED", "chunks": len(chunks), "digest": digest}

    def search(self, question: str, k: int = 5) -> list[Hit]:
        if not 1 <= k <= 100:
            raise RetrievalError("k must be between 1 and 100")
        if not question.strip():
            return []
        manifest = self._manifest()
        if manifest is None:
            raise RetrievalError("Qdrant namespace not indexed")
        if (
            manifest.get("model_id") != self.model_id
            or manifest.get("dimension") != self.dimension
            or manifest.get("kind") != "manifest"
        ):
            raise RetrievalError("Qdrant namespace model mismatch")
        if self._count() != manifest.get("chunks"):
            raise RetrievalError("Qdrant namespace incomplete")
        vectors = self.embedder.encode([question])
        if len(vectors) != 1:
            raise RetrievalError("invalid query embedding batch")
        vector = validate_vector(vectors[0], self.dimension)
        result = self._request(
            "POST",
            self._path("/points/query"),
            {
                "query": vector,
                "filter": {"must": [{"key": "kind", "match": {"value": "chunk"}}]},
                "limit": k,
                "with_payload": True,
                "with_vector": False,
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("points"), list):
            raise RetrievalError("invalid Qdrant search result")
        hits: list[Hit] = []
        for rank, point in enumerate(result["points"], start=1):
            payload = point["payload"]
            score = float(point["score"])
            if not math.isfinite(score):
                raise RetrievalError("non-finite Qdrant score")
            hits.append(
                Hit(
                    rank,
                    str(payload["chunk_id"]),
                    str(payload["document_id"]),
                    str(payload["source_path"]),
                    str(payload["source_url"]),
                    round(score, 8),
                )
            )
        return hits
