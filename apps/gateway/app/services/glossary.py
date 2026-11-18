"""Load authored help glossary from config/help/glossary.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.paths import resolve_repo_root


def glossary_path() -> Path:
    return resolve_repo_root() / "config" / "help" / "glossary.yaml"


def _normalize_term(raw: dict[str, Any]) -> dict[str, Any]:
    links_raw = raw.get("links") or []
    links: list[dict[str, str]] = []
    for link in links_raw:
        if not isinstance(link, dict):
            continue
        label = str(link.get("label") or "").strip()
        href = str(link.get("href") or "").strip()
        if label and href:
            links.append({"label": label, "href": href})

    aliases = raw.get("aliases") or []
    related = raw.get("related") or []
    return {
        "id": str(raw.get("id") or "").strip(),
        "term": str(raw.get("term") or "").strip(),
        "aliases": [str(a).strip() for a in aliases if str(a).strip()],
        "short": str(raw.get("short") or "").strip(),
        "body": " ".join(str(raw.get("body") or "").split()),
        "related": [str(r).strip() for r in related if str(r).strip()],
        "links": links,
    }


@lru_cache(maxsize=1)
def load_glossary(path: str | None = None) -> list[dict[str, Any]]:
    """Return normalized glossary terms. Empty list if file missing."""
    file_path = Path(path) if path else glossary_path()
    if not file_path.is_file():
        return []
    raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    terms_raw = raw.get("terms") or []
    terms: list[dict[str, Any]] = []
    for item in terms_raw:
        if not isinstance(item, dict):
            continue
        term = _normalize_term(item)
        if term["id"] and term["term"]:
            terms.append(term)
    return terms


def clear_glossary_cache() -> None:
    load_glossary.cache_clear()
