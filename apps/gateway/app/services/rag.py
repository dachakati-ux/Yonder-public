"""Persistent RAG corpus with lexical retrieval (embedding index is a future adapter)."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RagDocument:
    id: str
    title: str
    text: str
    source: str
    created_at: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class RagChunk:
    id: str
    document_id: str
    ordinal: int
    text: str
    title: str


@dataclass(frozen=True)
class RagHit:
    chunk: RagChunk
    score: float
    citation: dict[str, Any]


class Retriever(Protocol):
    def retrieve(self, query: str, *, k: int = 3) -> list[RagHit]: ...


class RagStore:
    """SQLite-backed document/chunk store under the control-plane data directory."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    title TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES rag_documents(id)
                );
                """
            )

    def ingest(
        self,
        *,
        title: str,
        text: str,
        source: str = "operator",
        metadata: dict[str, str] | None = None,
        document_id: str | None = None,
    ) -> RagDocument:
        doc_id = document_id or f"doc-{uuid.uuid4().hex[:12]}"
        created = _now()
        chunks = chunk_text(text)
        meta = metadata or {}
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM rag_chunks WHERE document_id = ?",
                (doc_id,),
            )
            conn.execute(
                """
                INSERT INTO rag_documents(id, title, text, source, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  title=excluded.title,
                  text=excluded.text,
                  source=excluded.source,
                  created_at=excluded.created_at,
                  metadata_json=excluded.metadata_json
                """,
                (doc_id, title, text, source, created, json_dumps(meta)),
            )
            for ordinal, chunk in enumerate(chunks):
                conn.execute(
                    """
                    INSERT INTO rag_chunks(id, document_id, ordinal, text, title)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (f"{doc_id}-c{ordinal}", doc_id, ordinal, chunk, title),
                )
        return RagDocument(
            id=doc_id,
            title=title,
            text=text,
            source=source,
            created_at=created,
            metadata=meta,
        )

    def list_documents(self) -> list[RagDocument]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, text, source, created_at, metadata_json "
                "FROM rag_documents ORDER BY created_at"
            ).fetchall()
        return [
            RagDocument(
                id=row["id"],
                title=row["title"],
                text=row["text"],
                source=row["source"],
                created_at=row["created_at"],
                metadata=json_loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def all_chunks(self) -> list[RagChunk]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, document_id, ordinal, text, title "
                "FROM rag_chunks ORDER BY document_id, ordinal"
            ).fetchall()
        return [
            RagChunk(
                id=row["id"],
                document_id=row["document_id"],
                ordinal=row["ordinal"],
                text=row["text"],
                title=row["title"],
            )
            for row in rows
        ]


class LexicalRetriever:
    """Token-overlap lexical retrieve — replaceable by an embeddings-backed adapter later."""

    def __init__(self, store: RagStore) -> None:
        self.store = store

    def retrieve(self, query: str, *, k: int = 3) -> list[RagHit]:
        tokens = {t.lower() for t in re.split(r"[^\w]+", query) if len(t) > 2}
        hits: list[RagHit] = []
        for chunk in self.store.all_chunks():
            hay = f"{chunk.title} {chunk.text}".lower()
            score = float(sum(1 for token in tokens if token in hay))
            if score <= 0:
                continue
            hits.append(
                RagHit(
                    chunk=chunk,
                    score=score,
                    citation={
                        "document_id": chunk.document_id,
                        "chunk_id": chunk.id,
                        "title": chunk.title,
                        "snippet": chunk.text[:240],
                        "score": score,
                    },
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        if hits:
            return hits[:k]
        # Fail closed: if corpus is empty, return nothing (caller decides).
        return []


def chunk_text(text: str, *, max_chars: int = 500) -> list[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + max_chars)
        if end < len(cleaned):
            split_at = cleaned.rfind(" ", start, end)
            if split_at > start:
                end = split_at
        chunks.append(cleaned[start:end].strip())
        start = end
    return [c for c in chunks if c]


def json_dumps(data: dict[str, str]) -> str:
    import json

    return json.dumps(data, ensure_ascii=True, sort_keys=True)


def json_loads(raw: str) -> dict[str, str]:
    import json

    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def seed_lab_corpus(store: RagStore, corpus_path: Path) -> int:
    """Import reviewed Lab YAML corpus into the durable store (idempotent by doc id)."""

    import yaml

    if not corpus_path.exists():
        return 0
    raw = yaml.safe_load(corpus_path.read_text(encoding="utf-8")) or {}
    count = 0
    for item in raw.get("documents", []):
        store.ingest(
            document_id=str(item["id"]),
            title=str(item["title"]),
            text=str(item["text"]),
            source="lab-corpus",
            metadata={"kind": "training_corpus"},
        )
        count += 1
    return count


def build_rag_context(hits: list[RagHit]) -> str:
    if not hits:
        return ""
    parts = []
    for index, hit in enumerate(hits, start=1):
        parts.append(
            f"[{index}] {hit.chunk.title} ({hit.chunk.document_id})\n{hit.chunk.text}"
        )
    return "\n\n".join(parts)
