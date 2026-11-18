"""Academy capacity snapshot — real Gateway readiness only."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.services.lab_live import alias_is_ready, load_runtime_config

TEACHING_ALIASES = (
    "yonder-chat",
    "yonder-embed-4b",
    "yonder-embed-8b",
    "yonder-rerank-4b",
    "yonder-coder",
    "yonder-image",
    "yonder-video",
)


def capacity_snapshot(request: Request) -> dict[str, Any]:
    """Report alias readiness from Gateway state — never invent green."""

    cfg = load_runtime_config()
    routes = getattr(request.app.state, "routes", None)
    known = set()
    if routes is not None:
        known = set(routes.aliases.keys())

    aliases_out = []
    for alias in TEACHING_ALIASES:
        present = alias in known
        ready = bool(present and alias_is_ready(request, alias))
        if not present:
            availability = "not_reported"
        elif ready:
            availability = "ready"
        else:
            availability = "not_ready"
        aliases_out.append(
            {
                "alias": alias,
                "present_in_routes": present,
                "ready": ready,
                "availability": availability,
            }
        )

    return {
        "lab_runtime": {
            "chat_alias": cfg.get("chat_alias"),
            "chat_alias_ready": alias_is_ready(request, str(cfg.get("chat_alias") or "")),
            "generation_enabled": bool(cfg.get("generation_enabled")),
        },
        "aliases": aliases_out,
        "cpu_vs_gpu": {
            "cpu_first": [
                "Academy progress",
                "Practice sandboxes / RAG indexes",
                "Missions / cohorts / prompt library",
                "Console + Gateway headroom",
            ],
            "gpu_for": [
                "Concurrent live Chat try-it",
                "Embeddings / rerank when READY",
                "Coder alias when READY",
                "Image / video Diffusers when READY (own GPU)",
            ],
        },
        "note": (
            "Availability comes from Gateway route state. "
            "Missing aliases are not_reported — never invented as READY."
        ),
    }
