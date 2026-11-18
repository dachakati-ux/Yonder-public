"""Hybrid docs retrieval math: embedding index, cosine, blend, rerank, fallback.

Uses a deterministic bag-of-words embedding so cosine similarity is meaningful
and the ranking logic is verifiable — this tests correctness, not a real model.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.docs_retrieval import (
    DocsRetrieverConfig,
    EmbeddingRetriever,
    HybridRetriever,
    VectorIndex,
    build_docs_retriever,
)
from app.services.rag import LexicalRetriever, RagStore

VOCAB = ["auth", "token", "worker", "gpu", "model", "docs", "search", "cosine", "fruit", "banana"]


def _embed(texts: list[str]) -> list[list[float]]:
    out = []
    for text in texts:
        words = {w.lower() for w in re.split(r"[^\w]+", text) if w}
        out.append([1.0 if term in words else 0.0 for term in VOCAB])
    return out


def _store(tmp_path: Path) -> RagStore:
    store = RagStore(tmp_path / "docs.sqlite3")
    store.ingest(document_id="d1", title="auth", text="worker auth token secret", source="t")
    store.ingest(document_id="d2", title="gpu", text="gpu model loading vram", source="t")
    store.ingest(document_id="d3", title="fruit", text="banana fruit smoothie", source="t")
    return store


def test_vector_index_build_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    index = VectorIndex(store)
    n1 = index.build(_embed)
    assert n1 == len(store.all_chunks())
    n2 = index.build(_embed)  # nothing new to embed
    assert n2 == 0
    assert len(index.vectors()) == len(store.all_chunks())


def test_embedding_retriever_ranks_by_cosine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    index = VectorIndex(store)
    index.build(_embed)
    retr = EmbeddingRetriever(store, index, lambda q: _embed([q])[0])
    hits = retr.retrieve("worker token auth", k=3)
    assert hits
    assert hits[0].chunk.title == "auth"  # shares the most terms


def test_hybrid_blends_and_respects_k(tmp_path: Path) -> None:
    store = _store(tmp_path)
    index = VectorIndex(store)
    index.build(_embed)
    hybrid = HybridRetriever(
        lexical=LexicalRetriever(store),
        embedding=EmbeddingRetriever(store, index, lambda q: _embed([q])[0]),
        alpha=0.5,
    )
    hits = hybrid.retrieve("gpu model vram", k=1)
    assert len(hits) == 1
    assert hits[0].chunk.title == "gpu"


def test_rerank_hook_reorders(tmp_path: Path) -> None:
    store = _store(tmp_path)
    index = VectorIndex(store)
    index.build(_embed)

    # Reranker that forces the 'fruit' doc to the top regardless of blend.
    def rerank(query: str, passages: list[str]) -> list[float]:
        return [10.0 if "banana" in p else 0.0 for p in passages]

    hybrid = HybridRetriever(
        lexical=LexicalRetriever(store),
        embedding=EmbeddingRetriever(store, index, lambda q: _embed([q])[0]),
        rerank=rerank,
    )
    # Query retrieves both 'auth' (worker/token) and 'fruit' (banana) into the
    # shortlist; the reranker then promotes 'fruit' over the stronger blend hit.
    hits = hybrid.retrieve("worker token banana", k=3)
    assert hits[0].chunk.title == "fruit"  # reranker won


def test_build_docs_retriever_falls_back_to_lexical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # embeddings disabled -> lexical
    r1 = build_docs_retriever(store, DocsRetrieverConfig(embeddings_enabled=False))
    assert isinstance(r1, LexicalRetriever)
    # enabled but no embed_fn -> lexical
    r2 = build_docs_retriever(store, DocsRetrieverConfig(embeddings_enabled=True))
    assert isinstance(r2, LexicalRetriever)
    # enabled with a broken embed_fn -> lexical (never fabricates vectors)
    def boom(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embed backend down")

    r3 = build_docs_retriever(
        store, DocsRetrieverConfig(embeddings_enabled=True, embed_texts=boom)
    )
    assert isinstance(r3, LexicalRetriever)
    # enabled with a working embed_fn -> hybrid
    r4 = build_docs_retriever(
        store, DocsRetrieverConfig(embeddings_enabled=True, embed_texts=_embed)
    )
    assert isinstance(r4, HybridRetriever)
    assert r4.retrieve("gpu model", k=1)[0].chunk.title == "gpu"
