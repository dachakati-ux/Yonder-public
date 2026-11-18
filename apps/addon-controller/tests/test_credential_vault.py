"""Encrypted credential store tests; never contacts a Docker daemon."""

from __future__ import annotations

import os
import sqlite3

import pytest
from controller.credential_vault import (
    CredentialVault,
    VaultError,
    create_master_key,
    read_master_key,
)


def setup_vault(tmp_path):
    key = tmp_path / "keys" / "master.key"
    db = tmp_path / "private" / "credentials.db"
    create_master_key(key)
    return CredentialVault(db, key), key, db


def test_encrypted_storage_never_persists_raw_secret(tmp_path):
    vault, key, db = setup_vault(tmp_path)
    secret = b"must-not-be-visible-in-raw-SQLite-abc123"
    revision = vault.put("convyx", "SERVICE_TOKEN", secret)
    assert revision == 1
    assert vault.metadata("convyx") == [{
        "addon_id": "convyx",
        "name": "SERVICE_TOKEN",
        "revision": 1,
        "updated_at": vault.metadata("convyx")[0]["updated_at"],
        "configured": True,
        "delivery": {"status": "never_delivered", "delivered_revision": None},
    }]
    with sqlite3.connect(db) as connection:
        payload = connection.execute(
            "SELECT ciphertext FROM credentials"
        ).fetchone()[0]
    assert secret not in bytes(payload)
    assert secret not in db.read_bytes()
    assert "SERVICE_TOKEN" in str(vault.metadata())
    assert "must-not" not in str(vault.metadata())
    assert len(read_master_key(key)) == 32


def test_credential_rotation_increments_revision_and_revocation(tmp_path):
    vault, _, _ = setup_vault(tmp_path)
    assert vault.put("convyx", "SERVICE_TOKEN", b"original") == 1
    assert vault.put("convyx", "SERVICE_TOKEN", b"replacement") == 2
    assert vault.metadata("convyx")[0]["revision"] == 2
    assert vault.revoke("convyx", "SERVICE_TOKEN") == 3
    assert vault.metadata("convyx")[0]["configured"] is False
    assert vault.put("convyx", "SERVICE_TOKEN", b"third") == 4
    assert vault.metadata("convyx")[0]["configured"] is True


def test_cross_addon_scope_and_invalid_identifiers(tmp_path):
    vault, _, _ = setup_vault(tmp_path)
    vault.put("workspaces", "DATABASE_PASSWORD", b"private")
    assert vault.metadata("convyx") == []
    assert len(vault.metadata("workspaces")) == 1
    for addon, name in [
        ("../escaped", "SERVICE_TOKEN"),
        ("workspaces", "../../ENV"),
        ("", "SERVICE_TOKEN"),
    ]:
        with pytest.raises(VaultError):
            vault.put(addon, name, b"bad")


def test_wrong_or_missing_key_rejected(tmp_path):
    vault, key, _ = setup_vault(tmp_path)
    key.unlink()
    with pytest.raises(VaultError):
        vault.put("convyx", "SERVICE_TOKEN", b"value")


def test_existing_master_key_cannot_be_overwritten(tmp_path):
    _, key, _ = setup_vault(tmp_path)
    before = key.read_bytes()
    with pytest.raises(VaultError):
        create_master_key(key)
    assert key.read_bytes() == before


def test_group_readable_master_key_refused(tmp_path):
    vault, key, _ = setup_vault(tmp_path)
    if os.name != "posix":
        pytest.skip("POSIX permissions test")
    os.chmod(key, 0o644)
    with pytest.raises(VaultError, match="permissions"):
        vault.put("convyx", "SERVICE_TOKEN", b"value")


def test_no_empty_or_oversized_credential_value(tmp_path):
    vault, _, _ = setup_vault(tmp_path)
    with pytest.raises(VaultError):
        vault.put("convyx", "SERVICE_TOKEN", b"")
    with pytest.raises(VaultError):
        vault.put("convyx", "SERVICE_TOKEN", b"x" * 16_385)


def test_audit_events_record_metadata_not_secret_values(tmp_path):
    vault, _, db = setup_vault(tmp_path)
    vault.put("convyx", "SERVICE_TOKEN", b"super-private-value")
    vault.put("convyx", "SERVICE_TOKEN", b"replacement-value")
    vault.revoke("convyx", "SERVICE_TOKEN")
    history = vault.audit_events("convyx")
    assert [event["action"] for event in history] == [
        "revoke", "set", "set"
    ]
    assert [event["revision"] for event in history] == [3, 2, 1]
    assert "super-private-value" not in str(history)
    assert "replacement-value" not in str(history)
    assert b"super-private-value" not in db.read_bytes()
    assert vault.audit_events("workspaces") == []


def test_encrypted_backup_and_restore_without_exporting_master_key(tmp_path):
    vault, key, db = setup_vault(tmp_path)
    value = b"never-export-plaintext-in-backup"
    vault.put("convyx", "SERVICE_TOKEN", value)
    target = tmp_path / "backups" / "credentials-snapshot.db"
    vault.backup(target)
    assert target.is_file()
    assert value not in target.read_bytes()
    assert key.read_bytes() not in target.read_bytes()
    restored = CredentialVault(target, key)
    assert restored.metadata("convyx")[0]["configured"] is True
    assert len(restored.audit_events("convyx")) == 1
    with pytest.raises(VaultError, match="already exists"):
        vault.backup(target)


def test_backup_requires_private_permissions(tmp_path):
    vault, _, _ = setup_vault(tmp_path)
    target = tmp_path / "backup.db"
    vault.backup(target)
    if os.name == "posix":
        assert target.stat().st_mode & 0o077 == 0
