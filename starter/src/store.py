"""Store and search vectors using PostgreSQL + pgvector (exact cosine search)."""
import json
from typing import Any

import psycopg

from .models import Chunk

# Only these metadata fields may be used as filters; they are real columns.
FILTER_COLUMNS = {"document_id", "section", "region"}


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is empty; copy starter/.env.example to starter/.env")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.database_url = database_url
        self.dimensions = dimensions
        self.table = f"chunks_{dimensions}d"

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, connect_timeout=5)

    def create_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    strategy      text NOT NULL,
                    chunk_id      text NOT NULL,
                    document_id   text NOT NULL,
                    source        text NOT NULL,
                    page          integer NOT NULL,
                    section       text,
                    region        text,
                    text          text NOT NULL,
                    content_hash  text NOT NULL,
                    metadata      jsonb NOT NULL,
                    embedding_model text NOT NULL,
                    embedding     vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )""")
            conn.execute(f"CREATE INDEX IF NOT EXISTS {self.table}_filter_idx "
                         f"ON {self.table} (strategy, region, section)")

    def existing_hashes(self, strategy: str) -> dict[str, tuple[str, str]]:
        """chunk_id -> (content_hash, embedding_model) for a strategy."""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT chunk_id, content_hash, embedding_model FROM {self.table} "
                                f"WHERE strategy = %s", (strategy,)).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    def delete_strategy(self, strategy: str) -> int:
        with self._connect() as conn:
            return conn.execute(f"DELETE FROM {self.table} WHERE strategy = %s", (strategy,)).rowcount

    def count(self, strategy: str) -> int:
        with self._connect() as conn:
            return conn.execute(f"SELECT count(*) FROM {self.table} WHERE strategy = %s",
                                (strategy,)).fetchone()[0]

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]],
               embedding_model: str) -> int:
        """Insert or update chunks by stable (strategy, chunk_id). Returns rows written."""
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        if len({c.chunk_id for c in chunks}) != len(chunks):
            raise ValueError("duplicate chunk_id in batch")
        for v in vectors:
            if len(v) != self.dimensions:
                raise ValueError(f"vector has {len(v)} dims, store expects {self.dimensions}")
        rows = [(strategy, c.chunk_id, c.document_id, c.source, c.page, c.metadata.get("section"),
                 c.metadata.get("region"), c.text, c.metadata["content_hash"],
                 json.dumps(c.metadata, ensure_ascii=False), embedding_model, _vector_literal(v))
                for c, v in zip(chunks, vectors)]
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(f"""
                INSERT INTO {self.table} (strategy, chunk_id, document_id, source, page, section,
                    region, text, content_hash, metadata, embedding_model, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::vector)
                ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                    document_id = EXCLUDED.document_id, source = EXCLUDED.source,
                    page = EXCLUDED.page, section = EXCLUDED.section, region = EXCLUDED.region,
                    text = EXCLUDED.text, content_hash = EXCLUDED.content_hash,
                    metadata = EXCLUDED.metadata, embedding_model = EXCLUDED.embedding_model,
                    embedding = EXCLUDED.embedding""", rows)
        return len(rows)

    def delete_obsolete(self, strategy: str, document_id: str, keep_ids: set[str]) -> int:
        """Remove chunks of a re-ingested document that no longer exist."""
        with self._connect() as conn:
            cur = conn.execute(f"DELETE FROM {self.table} WHERE strategy = %s AND document_id = %s "
                               f"AND NOT (chunk_id = ANY(%s))", (strategy, document_id, list(keep_ids)))
            return cur.rowcount

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if len(query_vector) != self.dimensions:
            raise ValueError("query vector dimension mismatch")
        where = ["strategy = %s"]
        params: list[Any] = [strategy]
        for key, value in (filters or {}).items():
            if key not in FILTER_COLUMNS:
                raise ValueError(f"filter on '{key}' is not allowed")
            where.append(f"{key} = %s")
            params.append(value)
        vec = _vector_literal(query_vector)
        sql = (f"SELECT chunk_id, document_id, source, page, section, region, text, metadata, "
               f"1 - (embedding <=> %s::vector) AS score FROM {self.table} "
               f"WHERE {' AND '.join(where)} ORDER BY embedding <=> %s::vector LIMIT %s")
        with self._connect() as conn:
            rows = conn.execute(sql, [vec, *params, vec, top_k]).fetchall()
        return [{"rank": i + 1, "chunk_id": r[0], "document_id": r[1], "source": r[2], "page": r[3],
                 "section": r[4], "region": r[5], "text": r[6], "metadata": r[7],
                 "score": round(float(r[8]), 6)} for i, r in enumerate(rows)]
