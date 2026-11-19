"""Model catalog behavior without network access or GPU assumptions."""

from __future__ import annotations

import pytest
from app.api import model_catalog


def test_catalog_item_metadata_is_safe_and_normalized():
    item = model_catalog._catalog_item(
        {
            "id": "example/Model-GGUF",
            "downloads": 120,
            "tags": ["gguf", "license:apache-2.0"],
            "gated": False,
        }
    )
    assert item["id"] == "example/Model-GGUF"
    assert item["license"] == "apache-2.0"
    assert item["has_gguf"] is True
    assert item["url"] == "https://huggingface.co/example/Model-GGUF"


@pytest.mark.parametrize("name", ["../secrets", "bad name", "a/b/c", "http://example.org"])
def test_repository_id_validation_rejects_unsafe_paths(name):
    assert model_catalog.REPO_ID.fullmatch(name) is None


def test_catalog_item_does_not_expose_unexpected_upstream_fields():
    item = model_catalog._catalog_item(
        {"id": "example/model", "token": "private", "siblings": [{"rfilename": ".env"}]}
    )
    assert "token" not in item
    assert "siblings" not in item


def test_memory_fit_estimator_and_presets():
    assert model_catalog.estimate_gguf_memory_fit(None)["fit"] == "unknown"
    fit_gpu = model_catalog.estimate_gguf_memory_fit(
        1024 * 1024 * 1024, available_vram_mb=4096, available_ram_mb=16384
    )
    assert fit_gpu["fit"] == "gpu_fit"
    assert fit_gpu["estimated_required_mb"] == 1741

    fit_ram = model_catalog.estimate_gguf_memory_fit(
        4 * 1024 * 1024 * 1024, available_vram_mb=2048, available_ram_mb=16384
    )
    assert fit_ram["fit"] == "cpu_ram_fit"

    fit_over = model_catalog.estimate_gguf_memory_fit(
        20 * 1024 * 1024 * 1024, available_vram_mb=2048, available_ram_mb=8192
    )
    assert fit_over["fit"] == "exceeds_memory"
    assert "lm_studio" in model_catalog.EXTERNAL_RUNTIME_PRESETS
    assert "mlx_lm" in model_catalog.EXTERNAL_RUNTIME_PRESETS


def test_search_catalog_filters_license_gguf_and_gated(monkeypatch):
    from types import MappingProxyType

    from app.context import RequestContextMiddleware
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yonder_contracts.principal import RequestPrincipal

    class _Provider:
        async def resolve(self, _request) -> RequestPrincipal:
            return RequestPrincipal(
                subject="viewer",
                principal_type="user",
                capabilities=("models.view",),
                attributes=MappingProxyType({}),
            )

    async def fake_get_json(_path, _params=None):
        return [
            {
                "id": "org/open-gguf",
                "tags": ["license:apache-2.0", "gguf"],
                "gated": False,
                "private": False,
                "sha": "abc123",
            },
            {
                "id": "org/gated-gguf",
                "tags": ["license:apache-2.0", "gguf"],
                "gated": True,
                "private": False,
            },
            {
                "id": "org/mit-safetensors",
                "tags": ["license:mit"],
                "gated": False,
                "private": False,
            },
        ]

    monkeypatch.setattr(model_catalog, "_get_json", fake_get_json)
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, provider=_Provider(), service_name="test")
    app.include_router(model_catalog.router)
    client = TestClient(app)

    res = client.get("/api/admin/catalog/search?license_filter=apache-2.0&gguf_only=true")
    assert res.status_code == 200
    assert [m["id"] for m in res.json()["models"]] == ["org/open-gguf"]
    assert res.json()["models"][0]["revision"] == "abc123"
    assert client.get("/api/admin/catalog/runtime-presets").status_code == 200
