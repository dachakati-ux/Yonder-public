"""End-to-end Gateway routes for bundled add-on installation and reporting."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.addon_service_registry import AddonServiceRegistry
from app.services.addons import AddonRegistry
from fastapi.testclient import TestClient


def test_addon_api_bundled_activation(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE", str(root / "config/environments/development.yaml")
    )
    from app.main import create_app

    app = create_app()
    app.state.addons = AddonRegistry(
        root / "addons", tmp_path / "addons-state.json"
    )
    app.state.addon_services = AddonServiceRegistry(
        tmp_path / "nonexistent-services.json"
    )
    with TestClient(app) as client:
        # Remote catalog must not be required to serve bundled data.
        async def no_remote(*, force=False):
            return None

        app.state.addons.refresh_remote = no_remote
        resp = client.get("/api/admin/addons")
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["addons"]}
        assert {"security-insights", "workspaces", "convyx"}.issubset(ids)

        blocked = client.post("/api/admin/addons/workspaces/install")
        assert blocked.status_code == 400
        installed = client.post("/api/admin/addons/security-insights/install")
        assert installed.status_code == 200
        assert installed.json()["installation"]["status"] == "installed"

        report = client.get("/api/admin/addons/security-insights/report")
        assert report.status_code == 200
        assert report.json()["checks"]
        assert "replace-with-a-very-long-secret-token" not in json.dumps(report.json())

        service = client.get("/api/admin/addons/security-insights/service/health")
        assert service.json()["status"] == "not_configured"
        assert client.get("/api/admin/addons/convyx/service/health").json() == {
            "addon_id": "convyx", "status": "not_installed"
        }

        removed = client.post("/api/admin/addons/security-insights/uninstall")
        assert removed.status_code == 200
        assert removed.json()["installation"]["status"] == "not_installed"


def test_gateway_proxies_scoped_setup_and_preflight(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE", str(root / "config/environments/development.yaml")
    )
    from app.main import create_app

    class StubController:
        def __init__(self):
            self.requests = []

        async def request(self, method, path, body=None):
            self.requests.append((method, path, body))
            if path.endswith("/preflight/security-insights"):
                return {
                    "addon_id": "security-insights",
                    "checks": [{"name": "configuration", "status": "ready", "message": "OK"}],
                    "can_submit": True,
                }
            if path.endswith("/setup/security-insights"):
                return {
                    "addon_id": "security-insights",
                    "fields": [],
                    "values": {},
                    "configured": True,
                    "note": "No credentials",
                }
            raise AssertionError("Unexpected route to external controller")

    app = create_app()
    app.state.addons = AddonRegistry(root / "addons", tmp_path / "addons.json")
    app.state.addons.install("security-insights")
    service = StubController()
    app.state.addon_controller = service
    with TestClient(app) as client:
        response = client.get(
            "/api/admin/addons/security-insights/controller/setup"
        )
        assert response.status_code == 200
        assert response.json()["configured"] is True
        response = client.put(
            "/api/admin/addons/security-insights/controller/setup",
            json={"values": {}},
        )
        assert response.status_code == 200
        response = client.get(
            "/api/admin/addons/security-insights/controller/preflight"
        )
        assert response.status_code == 200
        assert response.json()["can_submit"] is True
        assert client.get(
            "/api/admin/addons/workspaces/controller/preflight"
        ).status_code == 400
        assert len(service.requests) == 3
        assert service.requests[1] == (
            "PUT", "/v1/setup/security-insights", {"values": {}}
        )


def test_addon_ui_mounts_filters_installed_and_capabilities(tmp_path):
    from app.main import create_app
    from app.services.addons import AddonRegistry

    app = create_app()
    addons_dir = tmp_path / "addons"
    (addons_dir / "security-insights").mkdir(parents=True)
    (addons_dir / "security-insights" / "README.md").write_text("# Security Insights\n")
    (addons_dir / "security-insights" / "addon.json").write_text(
        '{"schema_version":1,"api_version":1,"id":"security-insights",'
        '"name":"Security Insights","version":"0.1.0","min_core_version":"0.1.0",'
        '"status":"available","install_mode":"bundled","permissions":["operations.view"],'
        '"docs":"addons/security-insights/README.md",'
        '"ui_extensions":[{"nav_label":"Audit View","mount_path":"/operations",'
        '"kind":"console_route","required_capabilities":["operations.view"]}]}'
    )
    (addons_dir / "catalog.json").write_text('{"schema_version":1,"addons":["security-insights"]}')
    app.state.addons = AddonRegistry(addons_dir, tmp_path / "addons.json")
    with TestClient(app) as client:
        # Before install -> no mounts advertised.
        assert client.get("/api/admin/addons/ui-mounts").json()["mounts"] == []
        app.state.addons.install("security-insights")
        mounts = client.get("/api/admin/addons/ui-mounts").json()["mounts"]
        assert len(mounts) == 1
        assert mounts[0]["mount_path"] == "/operations"
