"""Tests for M02.9 pluggable SecretBackend (LocalVaultBackend & VaultKv2Backend)."""

from __future__ import annotations

import os

import httpx
import pytest
from controller.credential_delivery import DeliveryError
from controller.credential_vault import CredentialVault, create_master_key
from controller.secret_backend import LocalVaultBackend, SecretBackendError, VaultKv2Backend


def test_local_vault_backend_health_and_metadata(tmp_path) -> None:
    key = tmp_path / "master.key"
    create_master_key(key)
    vault = CredentialVault(tmp_path / "vault.db", key)
    vault.put("convyx", "SERVICE_TOKEN", b"local-token")
    backend = LocalVaultBackend(vault)
    assert backend.health()["status"] == "ready"
    meta = backend.metadata("convyx")
    assert len(meta) == 1
    assert meta[0]["name"] == "SERVICE_TOKEN"


def test_vault_kv2_backend_enforces_https_scope_and_delivers(tmp_path, monkeypatch) -> None:
    with pytest.raises(SecretBackendError, match="HTTPS"):
        VaultKv2Backend(base_url="http://vault.internal:8200", token="tok")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Vault-Token"] == "vault-scoped-token"
        if request.url.path == "/v1/sys/health":
            return httpx.Response(200, json={"initialized": True, "sealed": False})
        if request.url.path == "/v1/secret/metadata/yonder/addons/convyx":
            return httpx.Response(200, json={"data": {"keys": ["SERVICE_TOKEN", "bad-name"]}})
        if request.url.path == "/v1/secret/data/yonder/addons/convyx/SERVICE_TOKEN":
            return httpx.Response(200, json={"data": {"data": {"value": "vault-secret-xyz"}}})
        return httpx.Response(404)

    backend = VaultKv2Backend(
        base_url="https://vault.example.org:8200",
        token="vault-scoped-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert backend.health()["status"] == "ready"
    meta = backend.metadata("convyx")
    assert [m["name"] for m in meta] == ["SERVICE_TOKEN"]

    dest = tmp_path / "secrets"
    dest.mkdir(mode=0o700)
    os.chmod(dest, 0o700)
    monkeypatch.setattr("controller.secret_backend._volume_details", lambda _addon: dest)

    with pytest.raises(DeliveryError):
        backend.deliver("workspaces", "SERVICE_TOKEN", approved_addon_ids={"convyx"})

    backend.deliver("convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"})
    assert (dest / "SERVICE_TOKEN").read_bytes() == b"vault-secret-xyz"
