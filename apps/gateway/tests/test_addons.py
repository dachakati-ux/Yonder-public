"""Bundled add-on registry and fail-closed installation tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.addons import AddonError, AddonRegistry


def make_registry(tmp_path: Path) -> AddonRegistry:
    root = tmp_path / "addons"
    root.mkdir()
    for name, status, mode in (
        ("security-insights", "available", "bundled"),
        ("identity-security", "planned", "not_available"),
    ):
        folder = root / name
        folder.mkdir()
        (folder / "addon.json").write_text(
            json.dumps({
                "schema_version": 1,
                "id": name,
                "name": name,
                "version": "0.1.0",
                "status": status,
                "install_mode": mode,
            }),
            encoding="utf-8",
        )
    return AddonRegistry(root, tmp_path / "state.json")


def test_bundled_addon_install_persists(tmp_path: Path):
    registry = make_registry(tmp_path)
    installed = registry.install("security-insights")
    assert installed["status"] == "installed"
    assert installed["changed"] is True
    assert registry.installed("security-insights")
    restored = AddonRegistry(registry.root, registry.state_path)
    assert restored.installed("security-insights")
    assert restored.install("security-insights")["changed"] is False
    assert restored.uninstall("security-insights")["changed"] is True
    assert not AddonRegistry(registry.root, registry.state_path).installed("security-insights")


def test_unreviewed_or_unimplemented_addons_are_blocked(tmp_path: Path):
    registry = make_registry(tmp_path)
    with pytest.raises(AddonError):
        registry.install("arbitrary-remote")
    with pytest.raises(AddonError):
        registry.install("identity-security")


def test_remote_catalog_does_not_grant_install_rights(tmp_path: Path):
    registry = make_registry(tmp_path)
    registry._remote_ids = {"arbitrary-remote", "security-insights"}
    assert [row["id"] for row in registry.snapshot()["addons"]] == [
        "identity-security", "security-insights"
    ]
    with pytest.raises(AddonError):
        registry.install("arbitrary-remote")


def test_security_insights_requires_install(tmp_path: Path):
    registry = make_registry(tmp_path)
    security = SimpleNamespace(
        mode=SimpleNamespace(value="development"),
        worker_shared_secret=None,
    )
    settings = SimpleNamespace(security=security)
    with pytest.raises(AddonError):
        registry.security_report(settings)
    registry.install("security-insights")
    checks = registry.security_report(settings)["checks"]
    assert any(c["id"] == "development_auth" and c["status"] == "warning" for c in checks)
    assert any(c["id"] == "worker_secret" and c["status"] == "warning" for c in checks)


def test_malicious_manifest_id_is_not_loaded(tmp_path: Path):
    registry = make_registry(tmp_path)
    path = registry.root / "security-insights" / "addon.json"
    data = json.loads(path.read_text())
    data["id"] = "../../evil"
    path.write_text(json.dumps(data))
    with pytest.raises(AddonError):
        registry.install("security-insights")
