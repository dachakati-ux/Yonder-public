"""SQLAlchemy persistence and conversation retention tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.persistence.factory import build_persistence
from app.routing.models import DeploymentRecord, DeploymentState, ModelAliasRoute, RouteState
from app.routing.registry import RouteRegistry
from app.services.memory_store import InMemoryConversationStore
from fastapi.testclient import TestClient
from yonder_configuration.models import ConversationRetentionConfig, YonderSettings


@pytest.mark.asyncio
async def test_memory_store_enforces_conversation_count_limit() -> None:
    store = InMemoryConversationStore(
        retention=ConversationRetentionConfig(
            max_conversations_per_principal=2,
            max_age_days=None,
            max_messages_per_conversation=50,
        )
    )
    await store.create_conversation(
        principal_subject="u1", title="a", model_alias="yonder-chat"
    )
    await store.create_conversation(
        principal_subject="u1", title="b", model_alias="yonder-chat"
    )
    await store.create_conversation(
        principal_subject="u1", title="c", model_alias="yonder-chat"
    )
    items = await store.list_conversations("u1")
    assert len(items) == 2


def test_sql_state_store_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "yonder.db"
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{db_path}")
    settings = YonderSettings(
        database_url=f"sqlite:///{db_path}",
        state_file=tmp_path / "gateway-state.json",
    )
    bundle = build_persistence(settings)
    assert bundle.backend == "sqlalchemy"

    routes = RouteRegistry()
    routes.upsert_deployment(
        DeploymentRecord(
            id="dep-1",
            model_definition_id="model-1",
            worker_id="worker-1",
            provider="mock",
            status=DeploymentState.STOPPED,
            request_count=3,
        )
    )
    routes.upsert_alias(
        ModelAliasRoute(
            alias="yonder-chat",
            deployment_id="dep-1",
            state=RouteState.UNAVAILABLE,
            experimental=True,
        )
    )
    bundle.state_store.save_from(routes)

    reloaded = RouteRegistry()
    bundle.state_store.load_into(reloaded)
    assert reloaded.get_deployment("dep-1") is not None
    assert reloaded.get_deployment("dep-1").status == DeploymentState.STOPPED
    assert reloaded.get_deployment("dep-1").active_requests == 0
    assert reloaded.get_alias("yonder-chat").state == RouteState.UNAVAILABLE


@pytest.mark.asyncio
async def test_sql_conversation_store_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "conv.db"
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{db_path}")
    settings = YonderSettings(
        database_url=f"sqlite:///{db_path}",
        state_file=tmp_path / "gateway-state.json",
        conversations=ConversationRetentionConfig(
            max_conversations_per_principal=10,
            max_age_days=None,
            max_messages_per_conversation=100,
        ),
    )
    store = build_persistence(settings).conversations
    convo = await store.create_conversation(
        principal_subject="alice", title="Hello", model_alias="yonder-chat"
    )
    message = await store.append_message(
        convo.id, "alice", role="user", content="hi there"
    )
    assert message is not None
    loaded = await store.get_conversation(convo.id, "alice")
    assert loaded is not None
    assert loaded.title == "Hello"
    assert len(loaded.messages) == 1
    assert loaded.messages[0].content == "hi there"


def test_gateway_with_sqlite_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "gateway.db"
    monkeypatch.setenv("YONDER_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("YONDER_STATE_FILE", str(tmp_path / "state.json"))
    # Import after env is set so module-level create_app picks up config.
    from app.main import create_app

    app = create_app()
    assert app.state.persistence.backend == "sqlalchemy"
    client = TestClient(app)
    response = client.get("/api/admin/operations/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["gateway"]["persistence_backend"] == "sqlalchemy"
    assert "admission" in body
    assert "capacity" in body
