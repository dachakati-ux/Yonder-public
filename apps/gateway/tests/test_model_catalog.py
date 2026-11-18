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
