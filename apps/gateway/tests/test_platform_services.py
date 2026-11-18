"""Platform tools / RAG / schema API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(ROOT / "config/environments/development.yaml"),
    )
    from app.main import create_app

    return TestClient(create_app())


def test_list_and_execute_tools(client: TestClient) -> None:
    listed = client.get("/v1/platform/tools")
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["tools"]}
    assert "lookup_weather" in names

    executed = client.post(
        "/v1/platform/tools/execute",
        json={"name": "lookup_weather", "arguments": {"city": "Seattle"}},
    )
    assert executed.status_code == 200
    body = executed.json()
    assert body["is_error"] is False
    assert "Seattle" in body["content"]


def test_tool_execute_rejects_unknown_tool(client: TestClient) -> None:
    response = client.post(
        "/v1/platform/tools/execute",
        json={"name": "not_a_real_tool", "arguments": {}},
    )
    assert response.status_code == 200
    assert response.json()["is_error"] is True


def test_schema_registry_lists_rubric(client: TestClient) -> None:
    response = client.get("/v1/platform/schemas")
    assert response.status_code == 200
    ids = {item["schema_id"] for item in response.json()["schemas"]}
    assert "yonder.lab.rubric.v1" in ids


def test_rag_ingest_and_retrieve(client: TestClient) -> None:
    ingested = client.post(
        "/v1/platform/rag/documents",
        json={
            "title": "Gateway-only traffic",
            "text": "Clients must call the Yonder Gateway; never workers directly.",
            "document_id": "test-gateway-only",
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["document"]["id"] == "test-gateway-only"

    retrieved = client.post(
        "/v1/platform/rag/retrieve",
        json={"query": "gateway workers", "k": 3},
    )
    assert retrieved.status_code == 200
    payload = retrieved.json()
    assert payload["retriever"] == "lexical"
    assert payload["citations"]
    assert "Gateway" in payload["context"] or "gateway" in payload["context"].lower()
