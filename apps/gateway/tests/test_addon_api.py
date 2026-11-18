"""End-to-end Gateway routes for bundled add-on installation and reporting."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.addons import AddonRegistry
from app.services.addon_service_registry import AddonServiceRegistry


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
