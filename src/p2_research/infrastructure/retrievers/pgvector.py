"""PostgreSQL/pgvector adapter for immutable, model-scoped dense retrieval.

Uses psycopg's parameter binding for all user values. The optional driver is
loaded only when a real database connection is requested.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any

from p2_research.application.retrieval.bm25 import Hit, RetrievalError
from p2_research.application.retrieval.semantic import Embedder, _normalized

EXPECTED_DIMENSION = 384  # sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2


def corpus_digest(chunks: Sequence[dict[str, Any]]) -> str:
    """Hash chunk identities and content, invariant to JSON formatting."""
    if not chunks:
        raise RetrievalError("empty corpus")
    entries = [(str(c["chunk_id"]), str(c["text"])) for c in chunks]
    if len({item[0] for item in entries}) != len(entries):
        raise RetrievalError("duplicate chunk identities")
    canonical = json.dumps(sorted(entries), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_vector(vector: Sequence[float], dimension: int = EXPECTED_DIMENSION) -> list[float]:
    if dimension < 1 or dimension > 2000:
        raise RetrievalError("invalid dimension")
    return list(_normalized(vector, dimension))


def pgvector_literal(vector: Sequence[float]) -> str:
    """Serialize a validated vector; the result is always a SQL parameter."""
    return "[" + ",".join(format(number, ".12g") for number in vector) + "]"


class PgvectorIndex:
    """Transactional idempotent indexing, scoped by corpus and embedding model.

    A separate index namespace deliberately pins a corpus digest and a model ID.
    Different model/corpus combinations cannot overwrite the same namespace.
    """

    def __init__(
        self,
        connection: Any,
        *,
        namespace: str,
        model_id: str,
        embedder: Embedder,
        dimension: int = EXPECTED_DIMENSION,
    ) -> None:
        if not namespace.strip() or not model_id.strip():
            raise RetrievalError("namespace and model_id are required")
        if dimension != EXPECTED_DIMENSION:
            raise RetrievalError("this schema requires 384-dimensional embeddings")
        self.connection = connection
        self.namespace = namespace
        self.model_id = model_id
        self.embedder = embedder
        self.dimension = dimension

    @staticmethod
    def connect(dsn: str, **kwargs: Any) -> Any:
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RetrievalError(
                "install optional pgvector dependencies: uv sync --extra pgvector"
            ) from exc
        return psycopg.connect(dsn, **kwargs)

    def initialize(self) -> None:
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS p2_vector_manifests (
                        namespace text PRIMARY KEY,
                        corpus_sha256 text NOT NULL,
                        model_id text NOT NULL,
                        dimension integer NOT NULL,
                        chunk_count integer NOT NULL
                    )"""
                )
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS p2_vectors (
                        namespace text NOT NULL REFERENCES p2_vector_manifests(namespace),
                        chunk_id text NOT NULL,
                        document_id text NOT NULL,
                        source_path text NOT NULL,
                        source_url text NOT NULL,
                        embedding vector(384) NOT NULL,
                        PRIMARY KEY (namespace, chunk_id)
                    )"""
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS p2_vectors_namespace_idx ON p2_vectors(namespace)"
                )

    def upsert_corpus(
        self, chunks: list[dict[str, Any]], *, batch_size: int = 32
    ) -> dict[str, Any]:
        if batch_size < 1:
            raise RetrievalError("batch_size must be positive")
        digest = corpus_digest(chunks)
        # Check manifest before embedding to fail early on mismatched identities.
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT corpus_sha256, model_id, dimension, chunk_count "
                "FROM p2_vector_manifests WHERE namespace = %s",
                (self.namespace,),
            )
            existing = cur.fetchone()
        expected = (digest, self.model_id, self.dimension, len(chunks))
        if existing is not None:
            if tuple(existing) != expected:
                raise RetrievalError("namespace manifest mismatch: refuse corpus/model replacement")
            with self.connection.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM p2_vectors WHERE namespace = %s", (self.namespace,)
                )
                count = int(cur.fetchone()[0])
            if count == len(chunks):
                return {"status": "ALREADY_INDEXED", "chunks": count, "digest": digest}
            raise RetrievalError("incomplete existing namespace: refuse partial overwrite")

        # All embedding batches are prepared before writing anything.
        rows: list[tuple[str, str, str, str, str, str]] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            encoded = self.embedder.encode([str(chunk["text"]) for chunk in batch])
            if len(encoded) != len(batch):
                raise RetrievalError("embedding batch size mismatch")
            for chunk, vector in zip(batch, encoded, strict=True):
                rows.append(
                    (
                        self.namespace,
                        str(chunk["chunk_id"]),
                        str(chunk["document_id"]),
                        str(chunk["source_path"]),
                        str(chunk["source_url"]),
                        pgvector_literal(validate_vector(vector, self.dimension)),
                    )
                )
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                # Prevent concurrent initializations of a single namespace.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self.namespace,))
                cur.execute(
                    "SELECT corpus_sha256 FROM p2_vector_manifests WHERE namespace = %s",
                    (self.namespace,),
                )
                if cur.fetchone() is not None:
                    raise RetrievalError("namespace created concurrently; retry after inspection")
                cur.execute(
                    "INSERT INTO p2_vector_manifests "
                    "(namespace, corpus_sha256, model_id, dimension, chunk_count) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (self.namespace, *expected),
                )
                cur.executemany(
                    "INSERT INTO p2_vectors (namespace, chunk_id, document_id, "
                    "source_path, source_url, embedding) VALUES (%s, %s, %s, %s, %s, %s::vector)",
                    rows,
                )
        return {"status": "INDEXED", "chunks": len(rows), "digest": digest}

    def search(self, question: str, k: int = 5) -> list[Hit]:
        if not 1 <= k <= 100:
            raise RetrievalError("k must be between 1 and 100")
        if not question.strip():
            return []
        encoded = self.embedder.encode([question])
        if len(encoded) != 1:
            raise RetrievalError("invalid query embedding batch")
        vector = pgvector_literal(validate_vector(encoded[0], self.dimension))
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, document_id, source_path, source_url, "
                "1 - (embedding <=> %s::vector) AS score "
                "FROM p2_vectors WHERE namespace = %s "
                "ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s",
                (vector, self.namespace, vector, k),
            )
            rows = cur.fetchall()
        hits = []
        for rank, (chunk_id, document_id, source_path, source_url, score) in enumerate(
            rows, start=1
        ):
            value = float(score)
            if not math.isfinite(value):
                raise RetrievalError("non-finite database similarity score")
            hits.append(Hit(rank, chunk_id, document_id, source_path, source_url, round(value, 8)))
        return hits
