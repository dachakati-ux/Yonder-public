"""Help glossary HTTP API — authored platform terms (Gateway-shared)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.context import get_principal, get_request_id
from app.services.glossary import glossary_path, load_glossary

router = APIRouter(prefix="/v1/help", tags=["help"])


@router.get("/glossary")
async def get_glossary(request: Request) -> dict[str, object]:
    """Return curated glossary terms from config/help/glossary.yaml.

    Authored content only — never invents hardware or deployment state.
    Requires an established request principal (same as other /v1 reads).
    """
    get_principal(request)
    get_request_id(request)
    terms = load_glossary()
    path = glossary_path()
    return {
        "source": "config/help/glossary.yaml",
        "path": str(path) if path.is_file() else "not_reported",
        "terms": terms,
        "count": len(terms),
    }
