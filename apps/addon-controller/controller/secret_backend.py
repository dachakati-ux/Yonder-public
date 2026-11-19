"""Pluggable secret backend contract: local encrypted vault and HashiCorp Vault KV v2 (M02.9)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .credential_delivery import DeliveryError, _open_directory, _volume_details, deliver
from .credential_vault import ADDON, SECRET, CredentialVault, VaultError


class SecretBackendError(RuntimeError):
    """Raised when a secret backend fails; must never include secret plaintext."""


class SecretBackend(Protocol):
    backend_name: str

    def health(self) -> dict[str, Any]: ...

    def metadata(self, addon_id: str) -> list[dict[str, Any]]: ...

    def deliver(
        self, addon_id: str, credential_name: str, *, approved_addon_ids: set[str]
    ) -> None: ...


class LocalVaultBackend:
    """Default local AES-256-GCM encrypted SQLite vault + offline delivery."""

    backend_name = "local_encrypted_vault"

    def __init__(self, vault: CredentialVault) -> None:
        self.vault = vault

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "status": "ready"}

    def metadata(self, addon_id: str) -> list[dict[str, Any]]:
        return self.vault.metadata(addon_id)

    def deliver(
        self, addon_id: str, credential_name: str, *, approved_addon_ids: set[str]
    ) -> None:
        deliver(self.vault, addon_id, credential_name, approved_addon_ids=approved_addon_ids)


class VaultKv2Backend:
    """Scoped HashiCorp Vault / OpenBao KV v2 secret backend adapter."""

    backend_name = "vault_kv_v2"

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        mount: str = "secret",
        prefix: str = "yonder/addons",
        http_client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(base_url.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise SecretBackendError("Vault URL must use HTTPS without embedded credentials.")
        if not token.strip():
            raise SecretBackendError("Vault token is required.")
        self.base_url = base_url.strip().rstrip("/")
        self._token = token.strip()
        self.mount = mount.strip("/")
        self.prefix = prefix.strip("/")
        self._http_client = http_client

    def _validate_scope(self, addon_id: str, credential_name: str | None = None) -> None:
        if not ADDON.fullmatch(addon_id):
            raise VaultError("Invalid add-on identifier")
        if credential_name is not None and not SECRET.fullmatch(credential_name):
            raise VaultError("Invalid credential identifier")

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"X-Vault-Token": self._token}
        if self._http_client is not None:
            resp = self._http_client.get(url, headers=headers, follow_redirects=False, timeout=10.0)
        else:
            with httpx.Client(follow_redirects=False, timeout=10.0) as client:
                resp = client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise SecretBackendError(f"Vault returned HTTP {resp.status_code}")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise SecretBackendError("Malformed Vault JSON response")
        return payload

    def health(self) -> dict[str, Any]:
        try:
            self._get("/v1/sys/health")
        except (SecretBackendError, httpx.HTTPError) as exc:
            return {"backend": self.backend_name, "status": "unavailable", "detail": str(exc)}
        return {"backend": self.backend_name, "status": "ready"}

    def metadata(self, addon_id: str) -> list[dict[str, Any]]:
        self._validate_scope(addon_id)
        payload = self._get(f"/v1/{self.mount}/metadata/{self.prefix}/{addon_id}?list=true")
        keys = ((payload.get("data") or {}).get("keys")) or []
        return [
            {
                "addon_id": addon_id,
                "name": str(name),
                "configured": True,
                "delivery": {"status": "vault_managed", "delivered_revision": None},
            }
            for name in keys
            if isinstance(name, str) and SECRET.fullmatch(name)
        ]

    def deliver(
        self, addon_id: str, credential_name: str, *, approved_addon_ids: set[str]
    ) -> None:
        from .credential_delivery import _atomic_write

        self._validate_scope(addon_id, credential_name)
        if addon_id not in approved_addon_ids:
            raise DeliveryError("Add-on is not in the local operator-approved deployment list")
        payload = self._get(f"/v1/{self.mount}/data/{self.prefix}/{addon_id}/{credential_name}")
        data = (payload.get("data") or {}).get("data") or {}
        value = data.get("value")
        if not isinstance(value, str) or not value:
            raise SecretBackendError("Vault secret payload missing non-empty 'value' field")
        directory = _open_directory(_volume_details(addon_id))
        try:
            _atomic_write(directory, credential_name, value.encode("utf-8"))
        finally:
            os.close(directory)


def build_secret_backend(
    vault_db: Path,
    vault_key: Path,
) -> SecretBackend:
    """Construct configured secret backend (default: LocalVaultBackend)."""
    kind = os.getenv("YONDER_CONTROLLER_SECRET_BACKEND", "local").strip().lower()
    if kind == "vault":
        return VaultKv2Backend(
            base_url=os.getenv("YONDER_VAULT_ADDR", ""),
            token=os.getenv("YONDER_VAULT_TOKEN", ""),
            mount=os.getenv("YONDER_VAULT_MOUNT", "secret"),
        )
    return LocalVaultBackend(CredentialVault(vault_db, vault_key))
