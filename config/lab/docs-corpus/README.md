# Docs corpus (coder_docs_search)

Drop-zone for documentation the coder agent can search via `coder_docs_search`.
Ingested into a RAG store by `scripts/ingest-docs.py` (offline, lexical — no
model or network needed).

Two kinds of content live in the searchable corpus:

1. **Yonder's own docs** — `docs/*.md`, `README.md`, `AGENTS.md`. Seeded
   automatically; authoritative and real.
2. **A dependency inventory** — generated from the repo manifests
   (`app.services.docs_corpus.build_dependency_inventory_markdown`). Lists the
   libraries Yonder uses, their versions, and where each is used. This is a
   **vendoring checklist**, not API documentation.
3. **Vendored library docs** — anything you drop into this folder
   (`.md`/`.txt`/`.rst`). This is where real third-party API docs go.

## Why this folder starts almost empty

A docs server exists to stop the agent hallucinating APIs. Seeding it with
invented library docs would defeat that, so we do **not** ship fabricated
third-party docs. Vendor the real docs for the libraries in the dependency
inventory here (offline copies from each project's official docs), then run:

    python scripts/ingest-docs.py config/lab/docs-corpus

On ai1 point it at wherever you stage vendored docs, e.g.:

    python scripts/ingest-docs.py --db /srv/yonder-storage/docs.sqlite3 /srv/yonder-storage/vendored-docs

Re-run after any dependency version bump to refresh the corpus (idempotent).
