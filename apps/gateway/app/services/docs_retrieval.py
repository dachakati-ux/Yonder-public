"""Hybrid docs retrieval — lexical + embedding cosine, optional rerank.

Phase 2 of the coder docs corpus. The embedding index persists chunk vectors
in the docs SQLite store; queries are embedded via a pluggable ``embed_texts``
callable wired to Yonder's local embedding alias. Everything degrades to
lexical retrieval when embeddings are unavailable — we never fabricate vectors
(a made-up embedding would silently corrupt ranking).

All retrievers satisfy the ``Retriever`` protocol in rag.py: ``retrieve(query,
*, k)`` -> ``list[RagHit]``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.services.rag import LexicalRetriever, RagHit, RagStore

# embed_texts(list_of_texts) -> list_of_vectors (same order). Sync; callers on
# the async path invoke retrievers via asyncio.to_thread.
EmbedFn = Callable[[list[str]], list[list[float]]]
# rerank(query, passages) -> list of scores (same order), higher is better.
RerankFn = Callable[[str, list[str]], list[float]]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class VectorIndex:
    """Persistent chunk-embedding index stored alongside the RAG chunks."""

    def __init__(self, store: RagStore) -> None:
        self.store = store
        self._init_db()

    def _init_db(self) -> None:
        with self.store._connect() as conn:  # noqa: SLF001 — same package, one store
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_embeddings (
                    chunk_id TEXT PRIMARY KEY,
                    dim INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    FOREIGN KEY(chunk_id) REFERENCES rag_chunks(id)
                );
                """
            )

    def _existing_ids(self) -> set[str]:
        with self.store._connect() as conn:  # noqa: SLF001
            rows = conn.execute("SELECT chunk_id FROM rag_embeddings").fetchall()
        return {row["chunk_id"] for row in rows}

    def build(self, embed_texts: EmbedFn, *, batch: int = 32) -> int:
        """Embed and persist any chunks not yet indexed. Idempotent.

        On an embedding failure it raises — the caller decides whether to fall
        back to lexical. Partial batches already persisted are kept.
        """
        chunks = self.store.all_chunks()
        have = self._existing_ids()
        pending = [c for c in chunks if c.id not in have]
        indexed = 0
        for start in range(0, len(pending), batch):
            group = pending[start : start + batch]
            vectors = embed_texts([c.text for c in group])
            if len(vectors) != len(group):
                raise ValueError("embed_texts returned wrong vector count")
            with self.store._connect() as conn:  # noqa: SLF001
                for chunk, vec in zip(group, vectors, strict=False):
                    conn.execute(
                        "INSERT OR REPLACE INTO rag_embeddings(chunk_id, dim, vector_json) "
                        "VALUES (?, ?, ?)",
                        (chunk.id, len(vec), json.dumps([float(x) for x in vec])),
                    )
            indexed += len(group)
        return indexed

    def vectors(self) -> dict[str, list[float]]:
        with self.store._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT chunk_id, vector_json FROM rag_embeddings"
            ).fetchall()
        return {row["chunk_id"]: json.loads(row["vector_json"]) for row in rows}


@dataclass
class EmbeddingRetriever:
    """Cosine-similarity retrieval over the persisted chunk vectors."""

    store: RagStore
    index: VectorIndex
    embed_query: Callable[[str], list[float]]

    def retrieve(self, query: str, *, k: int = 3) -> list[RagHit]:
        vecs = self.index.vectors()
        if not vecs:
            return []
        try:
            qvec = self.embed_query(query)
        except Exception:  # noqa: BLE001 — embedding failure => no semantic hits
            return []
        chunk_by_id = {c.id: c for c in self.store.all_chunks()}
        scored: list[RagHit] = []
        for chunk_id, vec in vecs.items():
            chunk = chunk_by_id.get(chunk_id)
            if chunk is None:
                continue
            score = _cosine(qvec, vec)
            if score <= 0.0:
                continue
            scored.append(
                RagHit(
                    chunk=chunk,
                    score=float(score),
                    citation={
                        "document_id": chunk.document_id,
                        "chunk_id": chunk.id,
                        "title": chunk.title,
                        "snippet": chunk.text[:240],
                        "score": float(score),
                        "retriever": "embedding",
                    },
                )
            )
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]


@dataclass
class HybridRetriever:
    """Blend lexical + embedding rankings; optionally rerank the shortlist.

    alpha weights the embedding score (0..1); (1-alpha) weights lexical. Scores
    are min-max normalized per retriever before blending so their scales match.
    """

    lexical: LexicalRetriever
    embedding: EmbeddingRetriever
    alpha: float = 0.5
    rerank: RerankFn | None = None
    shortlist: int = 10

    def retrieve(self, query: str, *, k: int = 3) -> list[RagHit]:
        pool = max(k, self.shortlist)
        lex = {h.chunk.id: h for h in self.lexical.retrieve(query, k=pool)}
        emb = {h.chunk.id: h for h in self.embedding.retrieve(query, k=pool)}
        lex_norm = _normalized({cid: h.score for cid, h in lex.items()})
        emb_norm = _normalized({cid: h.score for cid, h in emb.items()})
        blended: dict[str, float] = {}
        for cid in set(lex_norm) | set(emb_norm):
            blended[cid] = (
                self.alpha * emb_norm.get(cid, 0.0)
                + (1.0 - self.alpha) * lex_norm.get(cid, 0.0)
            )
        by_id = {**lex, **emb}
        ranked = sorted(blended.items(), key=lambda kv: kv[1], reverse=True)
        hits = [_rescore(by_id[cid], score) for cid, score in ranked if cid in by_id]
        if self.rerank is not None and hits:
            hits = self._apply_rerank(query, hits)
        return hits[:k]

    def _apply_rerank(self, query: str, hits: list[RagHit]) -> list[RagHit]:
        try:
            scores = self.rerank(query, [h.chunk.text for h in hits])
        except Exception:  # noqa: BLE001 — reranker down => keep blended order
            return hits
        if len(scores) != len(hits):
            return hits
        rescored = [_rescore(h, float(s)) for h, s in zip(hits, scores, strict=False)]
        rescored.sort(key=lambda h: h.score, reverse=True)
        return rescored


def _normalized(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi <= lo:
        return {cid: 1.0 for cid in scores}
    return {cid: (v - lo) / (hi - lo) for cid, v in scores.items()}


def _rescore(hit: RagHit, score: float) -> RagHit:
    citation = dict(hit.citation)
    citation["score"] = float(score)
    return RagHit(chunk=hit.chunk, score=float(score), citation=citation)


@dataclass
class DocsRetrieverConfig:
    embeddings_enabled: bool = False
    embed_texts: EmbedFn | None = None
    rerank: RerankFn | None = None
    alpha: float = 0.5
    _built: list[str] = field(default_factory=list)


def build_docs_retriever(store: RagStore, cfg: DocsRetrieverConfig):
    """Return a hybrid retriever when embeddings are usable, else lexical.

    Never fabricates vectors: if embeddings are disabled, no embed_fn is given,
    or index build fails, it returns the lexical retriever unchanged.
    """
    lexical = LexicalRetriever(store)
    if not cfg.embeddings_enabled or cfg.embed_texts is None:
        return lexical
    try:
        index = VectorIndex(store)
        index.build(cfg.embed_texts)
    except Exception:  # noqa: BLE001 — embedding backend unavailable => lexical
        return lexical

    def _embed_query(q: str) -> list[float]:
        return cfg.embed_texts([q])[0]

    embedding = EmbeddingRetriever(store=store, index=index, embed_query=_embed_query)
    return HybridRetriever(
        lexical=lexical, embedding=embedding, alpha=cfg.alpha, rerank=cfg.rerank
    )
