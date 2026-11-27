"""Onboarding diagnostics preserve routing and authorization boundaries."""

from fastapi.testclient import TestClient

from app.routing.models import DeploymentRecord, DeploymentState, ModelAliasRoute, RouteState


def test_setup_classifies_mock():
    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.get("/v1/onboarding/status")
        assert response.status_code == 200
        body = response.json()
        assert body["gateway_ready"] is True
        assert body["security_mode"] == "development"
        aliases = {item["alias"]: item for item in body["aliases"]}
        assert aliases["yonder-chat"]["kind"] == "mock"
        assert "yonder-embed" not in aliases


def test_setup_unavailable_routes():
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        route = app.state.routes.get_alias("yonder-chat")
        deployment = app.state.routes.get_deployment(route.deployment_id)
        deployment.provider = "unconfigured-provider"
        body = client.get("/v1/onboarding/status").json()
        assert body["aliases"][0]["status"] == "unavailable"
        deployment.provider = "mock"
        route.state = RouteState.DRAINING
        body = client.get("/v1/onboarding/status").json()
        assert body["aliases"][0]["status"] == "unavailable"


def test_setup_reports_candidate_without_infrastructure_details():
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        app.state.providers.register("test-real-adapter", object())
        app.state.routes.upsert_deployment(
            DeploymentRecord(
                id="test-external",
                model_definition_id="yonder-chat-small",
                worker_id="private-worker",
                provider="test-real-adapter",
                status=DeploymentState.READY,
                runtime_configuration={"base_url": "http://private-host:1234"},
            )
        )
        app.state.routes.upsert_alias(
            ModelAliasRoute(alias="test-model", deployment_id="test-external")
        )
        response = client.get("/v1/onboarding/status")
        assert {
            "alias": "test-model",
            "kind": "model",
            "status": "route_ready",
            "experimental": False,
        } in response.json()["aliases"]
        assert "private-host" not in response.text
        assert "private-worker" not in response.text


def test_setup_enforces_chat_and_experimental_permissions(monkeypatch):
    monkeypatch.setenv("YONDER_SECURITY__MODE", "trusted_ingress")
    from app.main import create_app

    client = TestClient(create_app())
    url = "/v1/onboarding/status"
    assert client.get(url).status_code == 401
    headers = {"X-Yonder-Trusted-Subject": "setup-user"}
    denied = client.get(url, headers={**headers, "X-Yonder-Trusted-Capabilities": "models.view"})
    assert denied.status_code == 403
    allowed = client.get(url, headers={**headers, "X-Yonder-Trusted-Capabilities": "chat.use"})
    assert allowed.status_code == 200
    assert all(not route["experimental"] for route in allowed.json()["aliases"])
