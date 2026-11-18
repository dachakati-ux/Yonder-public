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
                "api_version": 1,
                "min_core_version": "0.1.0",
                "max_core_version_exclusive": "1.0.0",
                "docs": f"addons/{name}/README.md",
                "id": name,
                "name": name,
                "version": "0.1.0",
                "status": status,
                "install_mode": mode,
                "dependencies": [],
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


def test_core_incompatibility_blocks_install(tmp_path: Path):
    registry = make_registry(tmp_path)
    path = registry.root / "security-insights" / "addon.json"
    item = json.loads(path.read_text())
    item["min_core_version"] = "999.0.0"
    item["max_core_version_exclusive"] = None
    path.write_text(json.dumps(item))
    addon = registry.snapshot()["addons"][-1]
    assert addon["compatibility"] == "core_too_old"
    assert addon["installable"] is False
    with pytest.raises(AddonError, match="incompatible"):
        registry.install("security-insights")


def test_dependencies_are_enforced_bidirectionally(tmp_path: Path):
    registry = make_registry(tmp_path)
    path = registry.root / "identity-security" / "addon.json"
    item = json.loads(path.read_text())
    item["status"] = "available"
    item["install_mode"] = "bundled"
    item["dependencies"] = ["security-insights"]
    path.write_text(json.dumps(item))
    with pytest.raises(AddonError, match="required"):
        registry.install("identity-security")
    registry.install("security-insights")
    registry.install("identity-security")
    with pytest.raises(AddonError, match="dependent"):
        registry.uninstall("security-insights")
    registry.uninstall("identity-security")
    registry.uninstall("security-insights")


def test_malformed_manifest_not_listed_or_activated(tmp_path: Path):
    registry = make_registry(tmp_path)
    path = registry.root / "security-insights" / "addon.json"
    item = json.loads(path.read_text())
    item["permissions"] = ["root.everything"]
    path.write_text(json.dumps(item))
    assert not any(row["id"] == "security-insights" for row in registry.snapshot()["addons"])
    with pytest.raises(AddonError):
        registry.install("security-insights")
