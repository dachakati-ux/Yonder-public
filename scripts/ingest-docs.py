#!/usr/bin/env python3
"""Ingest docs into the coder docs corpus (RAG store for coder_docs_search).

Reuses the gateway's RagStore. Two things it does:
  1. Seeds Yonder's own docs + the repo-sourced dependency inventory.
  2. Ingests any additional doc trees you point it at (your vendored library
     docs, on ai1). Doc-like files only: .md/.markdown/.txt/.rst.

Idempotent — re-running upserts by document id, so it's safe as a deploy /
refresh step. Offline: no network, no model needed (lexical retrieval).

Usage (from repo root, in the gateway venv):
  python scripts/ingest-docs.py                        # seed Yonder docs + inventory
  python scripts/ingest-docs.py /srv/vendored-docs ... # + vendored doc trees
  python scripts/ingest-docs.py --db /srv/yonder-storage/docs.sqlite3 /srv/docs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "gateway"))

from app.services.docs_corpus import ingest_docs_tree, seed_default_docs  # noqa: E402
from app.services.rag import LexicalRetriever, RagStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest docs into the coder docs corpus")
    parser.add_argument(
        "roots", nargs="*", help="Additional doc trees to ingest (vendored library docs)"
    )
    parser.add_argument(
        "--db",
        default=str(REPO_ROOT / "data" / "docs.sqlite3"),
        help="Docs RAG store path (default: data/docs.sqlite3)",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip seeding Yonder's own docs + dependency inventory",
    )
    parser.add_argument(
        "--query", help="After ingest, run a test retrieval and print hits"
    )
    args = parser.parse_args()

    store = RagStore(Path(args.db))
    if not args.no_seed:
        counts = seed_default_docs(store, REPO_ROOT)
        print("Seeded Yonder docs + inventory:", counts)
    for root in args.roots:
        n = ingest_docs_tree(store, Path(root), category="vendored")
        print(f"Ingested {n} docs from {root}")

    docs = store.list_documents()
    print(f"Corpus now holds {len(docs)} documents, {len(store.all_chunks())} chunks.")

    if args.query:
        hits = LexicalRetriever(store).retrieve(args.query, k=5)
        print(f"\nTop hits for {args.query!r}:")
        for h in hits:
            print(f"  [{h.score:.0f}] {h.chunk.title}: {h.chunk.text[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
