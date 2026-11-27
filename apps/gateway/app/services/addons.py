"""Versioned, reviewed add-on catalog and persistent activation state.

Remote GitHub data is discovery metadata ONLY. Installation is possible solely
for a bundled, reviewed add-on present in the running Gateway image.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Any

import httpx
from pydantic import ValidationError

from app.services.addon_contract import AddonManifest

ADDON_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CATALOG_URL = (
    "https://raw.githubusercontent.com/dachakati-ux/"
    "Yonder-public/main/addons/catalog.json"
)
CHECK_INTERVAL = 3600
MAX_CATALOG_BYTES = 128_000
MAX_ADDONS = 60
INSTALLABLE_MODE = "bundled"


class AddonError(ValueError):
    """A blocked add-on action, never a command execution error."""


class AddonRegistry:
    def __init__(self, root: Path, state_path: Path):
        self.root = root
        self.state_path = state_path
        self._lock = RLock()
        self._installed = self._read_state()
        self._remote_ids: set[str] | None = None
        self._remote_checked = 0.0
        self._remote_error: str | None = None

    def _read_state(self) -> dict[str, str]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                return {}
            installed = data.get("installed", {})
            if not isinstance(installed, dict):
                return {}
            return {
                key: value
                for key, value in installed.items()
                if isinstance(key, str)
                and ADDON_ID.fullmatch(key)
                and isinstance(value, str)
                and SEMVER.fullmatch(value)
            }
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".addons-", suffix=".json", dir=self.state_path.parent
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"schema_version": 1, "installed": self._installed},
                    stream,
                    indent=2,
                    sort_keys=True,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _bundled(self) -> dict[str, dict[str, Any]]:
        """Only vetted manifests physically included in the release image."""
        result: dict[str, dict[str, Any]] = {}
        if not self.root.is_dir():
            return result
        for directory in sorted(self.root.iterdir()):
            if directory.is_symlink() or not directory.is_dir():
                continue
            if not ADDON_ID.fullmatch(directory.name):
                continue
            manifest_path = directory / "addon.json"
            if manifest_path.is_symlink():
                continue
            try:
                if manifest_path.stat().st_size > 16_384:
                    continue
                item = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = AddonManifest.model_validate(item)
                if manifest.id != directory.name:
                    continue
                result[directory.name] = manifest.display()
            except (OSError, ValueError, ValidationError):
                continue
        return result

    def _active_ids(self, packaged: dict[str, dict[str, Any]]) -> set[str]:
        """Resolve usable installed add-ons, including transitive dependencies.

        Stored installation markers are not authorization: version drift,
        removed manifests, incompatible releases and dependency cycles fail closed.
        """
        candidates = {
            addon_id: AddonManifest.model_validate(item)
            for addon_id, item in packaged.items()
            if self._installed.get(addon_id) == item["version"]
            and item["status"] == "available"
            and item["install_mode"] == INSTALLABLE_MODE
        }
        active: set[str] = set()
        while True:
            newly_active = {
                addon_id for addon_id, manifest in candidates.items()
                if manifest.compatibility() == "compatible"
                and set(manifest.dependencies) <= active
            } - active
            if not newly_active:
                return active
            active.update(newly_active)

    def active(self, addon_id: str) -> bool:
        with self._lock:
            return addon_id in self._active_ids(self._bundled())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            packaged = self._bundled()
            active = self._active_ids(packaged)
            items = []
            for addon_id, item in sorted(packaged.items()):
                manifest = AddonManifest.model_validate(item)
                enabled = addon_id in self._installed
                compatibility = manifest.compatibility()
                missing_dependencies = [
                    dep for dep in manifest.dependencies if dep not in active
                ]
                installed_version = self._installed.get(addon_id)
                installable = (
                    item.get("status") == "available"
                    and item.get("install_mode") == INSTALLABLE_MODE
                    and compatibility == "compatible"
                    and not missing_dependencies
                )
                items.append({
                    "id": addon_id,
                    "name": item["name"],
                    "version": item["version"],
                    "summary": item.get("summary", ""),
                    "category": item.get("category", "other"),
                    "permissions": item.get("permissions", []),
                    "warning": item.get("warning", ""),
                    "requires_restart": bool(item.get("requires_restart", False)),
                    "status": "installed" if enabled else item["status"],
                    "installed_version": installed_version,
                    "api_version": manifest.api_version,
                    "min_core_version": manifest.min_core_version,
                    "max_core_version_exclusive": manifest.max_core_version_exclusive,
                    "compatibility": compatibility,
                    "dependencies": manifest.dependencies,
                    "missing_dependencies": missing_dependencies,
                    "config_schema_version": manifest.config_schema_version,
                    "service_id": manifest.service_id,
                    "ui_extensions": [
                        ext.model_dump(mode="json") for ext in manifest.ui_extensions
                    ],
                    "active": addon_id in active,
                    "requires_attention": enabled and addon_id not in active,
                    "installable": bool(installable and not enabled),
                    "removable": enabled,
                    "in_public_catalog": (
                        addon_id in self._remote_ids if self._remote_ids is not None else None
                    ),
                    "docs": item.get("docs"),
                })
            return {
                "addons": items,
                "source": "bundled_release",
                "remote_catalog": {
                    "url": CATALOG_URL,
                    "checked": self._remote_checked > 0,
                    "error": self._remote_error,
                    "available": self._remote_ids is not None,
                },
                "security": {
                    "remote_code_execution": False,
                    "install_requires_admin": True,
                    "unreviewed_addons_blocked": True,
                },
            }

    def installed(self, addon_id: str) -> bool:
        with self._lock:
            return addon_id in self._installed

    def install(self, addon_id: str) -> dict[str, Any]:
        with self._lock:
            packaged = self._bundled()
            item = packaged.get(addon_id)
            if item is None:
                raise AddonError("Add-on is not bundled in this Yonder release.")
            if item.get("status") != "available" or item.get("install_mode") != INSTALLABLE_MODE:
                raise AddonError("This add-on is not implemented or installable yet.")
            manifest = AddonManifest.model_validate(item)
            compatibility = manifest.compatibility()
            if compatibility != "compatible":
                raise AddonError(
                    f"Add-on is incompatible with Yonder Core: {compatibility}."
                )
            active = self._active_ids(packaged)
            missing = [
                dependency
                for dependency in manifest.dependencies
                if dependency not in active
            ]
            if missing:
                raise AddonError(
                    "Install required add-ons first: " + ", ".join(missing)
                )
            if addon_id in self._installed:
                if self._installed[addon_id] != item["version"]:
                    raise AddonError("Installed version differs; migration required.")
                return {"id": addon_id, "status": "installed", "changed": False}
            self._installed[addon_id] = item["version"]
            try:
                self._save_state()
            except OSError:
                del self._installed[addon_id]
                raise
            return {"id": addon_id, "status": "installed", "changed": True}

    def uninstall(self, addon_id: str) -> dict[str, Any]:
        with self._lock:
            if addon_id not in self._installed:
                return {"id": addon_id, "status": "not_installed", "changed": False}
            blocking = [
                name
                for name, item in self._bundled().items()
                if name in self._installed and addon_id in item.get("dependencies", [])
            ]
            if blocking:
                raise AddonError(
                    "Disable dependent add-ons first: " + ", ".join(blocking)
                )
            version = self._installed.pop(addon_id)
            try:
                self._save_state()
            except OSError:
                self._installed[addon_id] = version
                raise
            return {"id": addon_id, "status": "not_installed", "changed": True}

    async def refresh_remote(self, *, force: bool = False) -> None:
        if (
            not force
            and self._remote_checked
            and time.monotonic() - self._remote_checked < CHECK_INTERVAL
        ):
            return
        self._remote_checked = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=8.0,
                follow_redirects=False,
                headers={"User-Agent": "Yonder-Addons/0.1"},
            ) as client:
                response = await client.get(CATALOG_URL)
                response.raise_for_status()
                if len(response.content) > MAX_CATALOG_BYTES:
                    raise ValueError("Catalog too large")
                data = response.json()
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise ValueError("Invalid catalog version")
            addons = data.get("addons")
            if not isinstance(addons, list) or len(addons) > MAX_ADDONS:
                raise ValueError("Invalid catalog list")
            ids: set[str] = set()
            for entry in addons:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    raise ValueError("Invalid catalog item")
                addon_id = entry["id"]
                if not ADDON_ID.fullmatch(addon_id):
                    raise ValueError("Invalid catalog add-on ID")
                if entry.get("path") != f"addons/{addon_id}/addon.json":
                    raise ValueError("Invalid catalog path")
                ids.add(addon_id)
            self._remote_ids = ids
            self._remote_error = None
        except (httpx.HTTPError, ValueError, TypeError):
            # A network failure must neither enable remote code nor drop installed state.
            self._remote_error = "Could not verify GitHub catalog; bundled catalog is available."

    async def check_remote(self) -> None:
        await self.refresh_remote()

    def security_report(self, settings: Any) -> dict[str, Any]:
        if not self.active("security-insights"):
            raise AddonError("Security Insights is not active; check installation compatibility.")
        checks = []
        if settings.security.mode.value == "development":
            checks.append({
                "id": "development_auth",
                "status": "warning",
                "message": (
                    "Development authentication injects a mock user; "
                    "keep deployment local only."
                ),
            })
        else:
            checks.append({
                "id": "trusted_ingress",
                "status": "review",
                "message": (
                    "Verify reverse-proxy header stripping, identity "
                    "validation and network isolation."
                ),
            })
        checks.append({
            "id": "worker_secret",
            "status": "configured" if settings.security.worker_shared_secret else "warning",
            "message": (
                "Worker service secret is configured."
                if settings.security.worker_shared_secret
                else "No worker shared secret is configured."
            ),
        })
        checks.append({
            "id": "multi_user",
            "status": "review",
            "message": (
                "School/business deployments need tested SSO, "
                "per-user permissions and retention."
            ),
        })
        return {
            "checks": checks,
            "note": "Configuration hints only; not a security scan or compliance certification.",
        }
