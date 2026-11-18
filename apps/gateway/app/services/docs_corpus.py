"""Docs corpus ingestion + Yonder dependency inventory for coder_docs_search.

Two content sources, both real:

* **Yonder's own docs** (``docs/*.md``, ``README.md``, ``AGENTS.md``) — the
  authoritative documentation for the Yonder apps, ingested as-is.
* **A dependency inventory** generated from the repo's own manifests
  (``pyproject.toml`` / ``package.json``): names, version constraints, role,
  and which Yonder component pulls each one in.

The inventory is a *checklist*, NOT third-party API documentation. A docs
server exists to stop the agent hallucinating APIs; seeding it with invented
library docs would defeat that. Real library docs must be vendored offline
(see docs/coder-live-canary.md); this inventory tells you exactly which ones.
"""

from __future__ import annotations

from pathlib import Path

from app.services.rag import RagStore

DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".rst"}

# Sourced from apps/*/pyproject.toml, packages/*/pyproject.toml, and
# apps/console/package.json. Role/import are factual identifiers, not docs.
YONDER_DEPENDENCIES: list[dict[str, str]] = [
    {"name": "fastapi", "constraint": ">=0.115.0", "import_name": "fastapi",
     "used_by": "gateway, worker", "role": "ASGI web framework for HTTP APIs",
     "docs": "https://fastapi.tiangolo.com/"},
    {"name": "uvicorn[standard]", "constraint": ">=0.32.0", "import_name": "uvicorn",
     "used_by": "gateway, worker", "role": "ASGI server that runs the apps",
     "docs": "https://www.uvicorn.org/"},
    {"name": "httpx", "constraint": ">=0.27.0", "import_name": "httpx",
     "used_by": "gateway, worker", "role": "HTTP client for provider, worker, and MCP calls",
     "docs": "https://www.python-httpx.org/"},
    {"name": "python-multipart", "constraint": ">=0.0.9", "import_name": "multipart",
     "used_by": "gateway", "role": "multipart/form-data parsing",
     "docs": "https://github.com/Kludex/python-multipart"},
    {"name": "sqlalchemy", "constraint": ">=2.0.36", "import_name": "sqlalchemy",
     "used_by": "gateway", "role": "ORM / SQL persistence layer",
     "docs": "https://docs.sqlalchemy.org/en/20/"},
    {"name": "psycopg[binary]", "constraint": ">=3.2.0", "import_name": "psycopg",
     "used_by": "gateway", "role": "PostgreSQL driver",
     "docs": "https://www.psycopg.org/psycopg3/docs/"},
    {"name": "alembic", "constraint": ">=1.14.0", "import_name": "alembic",
     "used_by": "gateway", "role": "database schema migrations",
     "docs": "https://alembic.sqlalchemy.org/en/latest/"},
    {"name": "psutil", "constraint": ">=6.1.0", "import_name": "psutil",
     "used_by": "worker", "role": "host CPU/memory/process metrics",
     "docs": "https://psutil.readthedocs.io/en/latest/"},
    {"name": "pydantic", "constraint": ">=2.9.0", "import_name": "pydantic",
     "used_by": "contracts, configuration", "role": "typed data models + validation",
     "docs": "https://docs.pydantic.dev/latest/"},
    {"name": "pydantic-settings", "constraint": ">=2.5.0", "import_name": "pydantic_settings",
     "used_by": "configuration", "role": "layered settings loading",
     "docs": "https://docs.pydantic.dev/latest/concepts/pydantic_settings/"},
    {"name": "pyyaml", "constraint": ">=6.0.2", "import_name": "yaml",
     "used_by": "configuration", "role": "YAML config parsing",
     "docs": "https://pyyaml.org/wiki/PyYAMLDocumentation"},
    {"name": "prometheus-client", "constraint": ">=0.21.0", "import_name": "prometheus_client",
     "used_by": "telemetry", "role": "Prometheus metrics export",
     "docs": "https://prometheus.github.io/client_python/"},
    {"name": "next", "constraint": "^15.5.21", "import_name": "next",
     "used_by": "console", "role": "React framework for the operator console",
     "docs": "https://nextjs.org/docs"},
    {"name": "react", "constraint": "^19.0.0", "import_name": "react",
     "used_by": "console", "role": "UI component library",
     "docs": "https://react.dev/reference/react"},
    {"name": "react-dom", "constraint": "^19.0.0", "import_name": "react-dom",
     "used_by": "console", "role": "React DOM renderer",
     "docs": "https://react.dev/reference/react-dom"},
    {"name": "react-markdown", "constraint": "^10.1.0", "import_name": "react-markdown",
     "used_by": "console", "role": "render markdown (chat, docs) in the console",
     "docs": "https://github.com/remarkjs/react-markdown"},
    {"name": "typescript", "constraint": "^5.7.0", "import_name": "typescript",
     "used_by": "console (dev)", "role": "typed JavaScript for the console",
     "docs": "https://www.typescriptlang.org/docs/"},
    {"name": "vitest", "constraint": "^4.1.10", "import_name": "vitest",
     "used_by": "console (dev)", "role": "console unit test runner",
     "docs": "https://vitest.dev/guide/"},
]


def build_dependency_inventory_markdown() -> str:
    """Render the repo-sourced dependency inventory as a corpus document."""
    lines = [
        "# Yonder dependency inventory",
        "",
        "Libraries Yonder currently depends on, sourced from the repo manifests.",
        "This is an inventory and a vendoring checklist — **not** a substitute",
        "for each library's real documentation. Vendor the official docs for the",
        "libraries below into the docs corpus for full API lookups.",
        "",
    ]
    for dep in YONDER_DEPENDENCIES:
        lines.append(f"## {dep['name']} {dep['constraint']}")
        lines.append(f"- Import: `{dep['import_name']}`")
        lines.append(f"- Used by: {dep['used_by']}")
        lines.append(f"- Role: {dep['role']}")
        lines.append(f"- Official docs (vendor these): {dep['docs']}")
        lines.append("")
    return "\n".join(lines)


def ingest_docs_tree(
    store: RagStore,
    root: Path,
    *,
    category: str,
    repo_root: Path | None = None,
) -> int:
    """Ingest every doc-like file under ``root`` (or a single file). Idempotent."""
    root = Path(root)
    if not root.exists():
        return 0
    files = (
        [root]
        if root.is_file()
        else [
            p
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix.lower() in DOC_EXTENSIONS
        ]
    )
    count = 0
    for path in files:
        try:
            rel = path.relative_to(repo_root) if repo_root else path
        except ValueError:
            rel = path
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        doc_id = "docs-" + category + "-" + str(rel).replace("/", "_").replace(".", "_")
        store.ingest(
            document_id=doc_id,
            title=str(rel),
            text=text,
            source=f"docs:{category}",
            metadata={"category": category, "path": str(rel)},
        )
        count += 1
    return count


def build_gateway_embed_fn(routes: object, providers: object, alias: str):
    """Sync embed_texts bridging to the gateway's embedding provider, or None.

    Resolves the embedding alias to a loaded deployment and calls the provider.
    Returns None when the alias is not resolvable/ready — the caller then keeps
    lexical retrieval instead of fabricating vectors. Runs the async provider
    via asyncio.run (safe: the coder tool executes retrievers off the loop).
    """
    import asyncio

    from yonder_contracts.embeddings import EmbeddingRequest

    from app.routing.resolve import RouteResolutionError, resolve_route

    resolved = resolve_route(routes, alias)
    if isinstance(resolved, RouteResolutionError):
        return None
    deployment = resolved.deployment
    provider = providers.get_embedding(deployment.provider)  # type: ignore[attr-defined]
    if provider is None:
        return None

    def _embed(texts: list[str]) -> list[list[float]]:
        request = EmbeddingRequest(
            request_id="docs-index",
            model_alias=alias,
            deployment_id=deployment.id,
            inputs=tuple(texts),
        )
        result = asyncio.run(provider.embed(request))
        return [list(vec.embedding) for vec in result.vectors]

    return _embed


def seed_default_docs(store: RagStore, repo_root: Path) -> dict[str, int]:
    """Seed the docs corpus with Yonder's own docs + the dependency inventory.

    Idempotent (ingest upserts by doc id). Real content only.
    """
    counts: dict[str, int] = {}
    counts["yonder_docs"] = ingest_docs_tree(
        store, repo_root / "docs", category="yonder", repo_root=repo_root
    )
    for top in ("README.md", "AGENTS.md"):
        counts[top] = ingest_docs_tree(
            store, repo_root / top, category="yonder", repo_root=repo_root
        )
    # Dependency inventory (generated from the manifests).
    store.ingest(
        document_id="docs-inventory-yonder-dependencies",
        title="yonder-dependencies.md",
        text=build_dependency_inventory_markdown(),
        source="docs:inventory",
        metadata={"category": "inventory", "path": "yonder-dependencies.md"},
    )
    counts["dependency_inventory"] = 1
    # Any operator-vendored docs dropped into the corpus dir.
    corpus_dir = repo_root / "config" / "lab" / "docs-corpus"
    counts["vendored"] = ingest_docs_tree(
        store, corpus_dir, category="vendored", repo_root=repo_root
    )
    return counts
