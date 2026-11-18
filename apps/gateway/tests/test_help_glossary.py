"""Help glossary API tests."""

from __future__ import annotations

from pathlib import Path

from app.main import create_app
from app.services.glossary import clear_glossary_cache, load_glossary
from fastapi.testclient import TestClient


def test_load_glossary_from_repo_yaml() -> None:
    clear_glossary_cache()
    terms = load_glossary()
    ids = {t["id"] for t in terms}
    assert "alias" in ids
    assert "ready" in ids
    assert "placement" in ids
    alias = next(t for t in terms if t["id"] == "alias")
    assert alias["term"] == "Alias"
    assert "yonder-chat" in alias["aliases"]
    assert alias["links"]


def test_get_glossary_endpoint() -> None:
    clear_glossary_cache()
    client = TestClient(create_app())
    response = client.get("/v1/help/glossary")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "config/help/glossary.yaml"
    assert body["count"] >= 10
    assert any(t["id"] == "ready" for t in body["terms"])
    assert all("short" in t and "body" in t for t in body["terms"])


def test_load_glossary_missing_file(tmp_path: Path) -> None:
    clear_glossary_cache()
    assert load_glossary(str(tmp_path / "missing.yaml")) == []
    clear_glossary_cache()
