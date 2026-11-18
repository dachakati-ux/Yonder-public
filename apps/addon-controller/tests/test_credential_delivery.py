"""Credential delivery tests: fake Docker volume, no daemon, no network."""
from __future__ import annotations

import os

import pytest
from controller.credential_delivery import DeliveryError, deliver, remove_delivered
from controller.credential_vault import CredentialVault, VaultError, create_master_key


def vault_fixture(tmp_path):
    key = tmp_path / "master.key"
    create_master_key(key)
    vault = CredentialVault(tmp_path / "vault.db", key)
    vault.put("convyx", "SERVICE_TOKEN", b"confidential-agent-token")
    vault.put("workspaces", "SERVICE_TOKEN", b"other-tenant-token")
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    return vault, directory


def test_offline_scoped_delivery_and_rotation(tmp_path, monkeypatch):
    vault, directory = vault_fixture(tmp_path)
    monkeypatch.setattr(
        "controller.credential_delivery._volume_details", lambda _addon: directory
    )
    deliver(
        vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"}
    )
    target = directory / "SERVICE_TOKEN"
    assert target.read_bytes() == b"confidential-agent-token"
    assert target.stat().st_mode & 0o077 == 0
    assert (directory / "SERVICE_TOKEN").is_file()

    vault.put("convyx", "SERVICE_TOKEN", b"replacement-agent-token")
    deliver(
        vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"}
    )
    assert target.read_bytes() == b"replacement-agent-token"
    assert list(directory.iterdir()) == [target]


def test_unapproved_addon_cannot_decrypt_or_deliver(tmp_path, monkeypatch):
    vault, directory = vault_fixture(tmp_path)
    called = []

    def location(_addon):
        called.append(True)
        return directory

    monkeypatch.setattr("controller.credential_delivery._volume_details", location)
    with pytest.raises(DeliveryError, match="not in"):
        deliver(
            vault, "workspaces", "SERVICE_TOKEN", approved_addon_ids={"convyx"}
        )
    assert not called
    assert list(directory.iterdir()) == []


def test_revoke_removes_only_authorized_delivered_file(tmp_path, monkeypatch):
    vault, directory = vault_fixture(tmp_path)
    monkeypatch.setattr(
        "controller.credential_delivery._volume_details", lambda _addon: directory
    )
    deliver(vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"})
    with pytest.raises(DeliveryError, match="Revoke"):
        remove_delivered(
            vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"}
        )
    vault.revoke("convyx", "SERVICE_TOKEN")
    with pytest.raises(VaultError, match="revoked"):
        deliver(vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"})
    remove_delivered(
        vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"}
    )
    assert not (directory / "SERVICE_TOKEN").exists()


def test_delivery_refuses_symlink_target_or_public_directory(tmp_path, monkeypatch):
    vault, directory = vault_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("never touch")
    (directory / "SERVICE_TOKEN").symlink_to(outside)
    monkeypatch.setattr(
        "controller.credential_delivery._volume_details", lambda _addon: directory
    )
    with pytest.raises(DeliveryError, match="not a regular"):
        deliver(vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"})
    assert outside.read_text() == "never touch"

    (directory / "SERVICE_TOKEN").unlink()
    os.chmod(directory, 0o755)
    with pytest.raises(DeliveryError, match="private"):
        deliver(vault, "convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"})


def test_delivery_refuses_cross_scope_and_path_traversal(tmp_path, monkeypatch):
    vault, directory = vault_fixture(tmp_path)
    monkeypatch.setattr(
        "controller.credential_delivery._volume_details", lambda _addon: directory
    )
    with pytest.raises(VaultError):
        deliver(vault, "convyx", "../TOKEN", approved_addon_ids={"convyx"})
    with pytest.raises(VaultError):
        deliver(vault, "../convyx", "SERVICE_TOKEN", approved_addon_ids={"convyx"})
    assert list(directory.iterdir()) == []


def test_docker_volume_policy_and_host_mountpoint_are_validated(
    tmp_path, monkeypatch
):
    import json
    from types import SimpleNamespace
    from controller.credential_delivery import _volume_details

    directory = tmp_path / "secrets"
    directory.mkdir()
    details = {
        "Name": "yonder-addon-secrets-convyx",
        "Driver": "local",
        "Labels": {
            "io.yonder.approved": "true",
            "io.yonder.addon": "convyx",
            "io.yonder.purpose": "secrets",
        },
        "Mountpoint": str(directory),
    }

    def stub_run(*args, **kwargs):
        assert args[0] == [
            "/usr/bin/docker", "volume", "inspect",
            "yonder-addon-secrets-convyx",
        ]
        assert kwargs.get("capture_output") is True
        return SimpleNamespace(stdout=json.dumps([details]).encode())

    monkeypatch.setattr("controller.credential_delivery.subprocess.run", stub_run)
    assert _volume_details("convyx") == directory
    details["Labels"]["io.yonder.addon"] = "workspaces"
    with pytest.raises(DeliveryError, match="not approved"):
        _volume_details("convyx")
    details["Labels"]["io.yonder.addon"] = "convyx"
    details["Driver"] = "unsafe-volume-plugin"
    with pytest.raises(DeliveryError, match="not approved"):
        _volume_details("convyx")
