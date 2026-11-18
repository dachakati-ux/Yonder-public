"""Offline backup integrity and sandbox-restore safety contract."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from controller.backup_verifier import (
    BackupError,
    create_offline_bundle,
    verify_bundle,
    verify_restore_into_empty_directory,
)

IMAGE = "ghcr.io/example/yonder-addon@sha256:" + "a" * 64


def bundle(tmp_path, *, quiesced=True, image=IMAGE, when=None):
    root = tmp_path / "backups"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    content = b"sqlite-export-bytes\x00safe"
    (root / "database.dump").write_bytes(content)
    manifest = {
        "schema_version": 1,
        "addon_id": "demo-addon",
        "version": "1.0.0",
        "image": image,
        "created_at": (when or datetime.now(UTC)).isoformat(),
        "source_quiesced": quiesced,
        "backup_kind": "database_export",
        "files": [{
            "name": "database.dump",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def check(root):
    return verify_bundle(
        root, addon_id="demo-addon", version="1.0.0", image=IMAGE,
    )


def test_quiesced_backup_and_separate_restore_pass(tmp_path):
    root = bundle(tmp_path)
    verified = check(root)
    assert verified["status"] == "verified_contents_only"
    assert verified["restore_tested"] is False
    sandbox = tmp_path / "restore"
    sandbox.mkdir(mode=0o700)
    os.chmod(sandbox, 0o700)
    result = verify_restore_into_empty_directory(
        root, sandbox, addon_id="demo-addon", version="1.0.0", image=IMAGE,
    )
    assert result["restore_tested"] is True
    assert result["migration_safe"] is False
    assert (sandbox / "database.dump").read_bytes() == (
        root / "database.dump"
    ).read_bytes()


def test_tampered_backup_is_rejected(tmp_path):
    root = bundle(tmp_path)
    (root / "database.dump").write_bytes(b"tampered")
    with pytest.raises(BackupError, match="checksum"):
        check(root)


def test_wrong_release_and_unquiesced_backup_fail(tmp_path):
    root = bundle(tmp_path, quiesced=False)
    with pytest.raises(BackupError, match="quiesced"):
        check(root)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["source_quiesced"] = True
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match="different release"):
        verify_bundle(
            root, addon_id="another-addon", version="1.0.0", image=IMAGE,
        )


def test_backup_expiry_and_future_timestamp_rejected(tmp_path):
    root = bundle(tmp_path, when=datetime.now(UTC) - timedelta(days=3))
    with pytest.raises(BackupError, match="freshness"):
        check(root)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["created_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match="freshness"):
        check(root)


def test_symlink_and_extra_files_rejected(tmp_path):
    root = bundle(tmp_path)
    (root / "other").write_text("extra")
    with pytest.raises(BackupError, match="Unexpected"):
        check(root)
    (root / "other").unlink()
    (root / "database.dump").unlink()
    (root / "database.dump").symlink_to(tmp_path / "outside")
    with pytest.raises(BackupError):
        check(root)


def test_duplicate_and_traversal_paths_rejected(tmp_path):
    root = bundle(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"][0]["name"] = "../outside"
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match="manifest"):
        check(root)
    manifest["files"][0]["name"] = "database.dump"
    manifest["files"].append(dict(manifest["files"][0]))
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match="manifest"):
        check(root)


def test_existing_or_public_restore_target_refused(tmp_path):
    root = bundle(tmp_path)
    sandbox = tmp_path / "restore"
    sandbox.mkdir(mode=0o700)
    os.chmod(sandbox, 0o700)
    (sandbox / "untouched").write_text("preserved")
    with pytest.raises(BackupError, match="empty"):
        verify_restore_into_empty_directory(
            root, sandbox, addon_id="demo-addon", version="1.0.0", image=IMAGE,
        )
    assert (sandbox / "untouched").read_text() == "preserved"
    (sandbox / "untouched").unlink()
    os.chmod(sandbox, 0o755)
    if os.name == "posix":
        with pytest.raises(BackupError, match="private"):
            verify_restore_into_empty_directory(
                root, sandbox, addon_id="demo-addon",
                version="1.0.0", image=IMAGE,
            )


def test_create_offline_bundle_requires_quiescence_and_preserves_source(tmp_path):
    source = tmp_path / "export.dump"
    source.write_bytes(b"safe-offline-export")
    destination = tmp_path / "bundle"
    with pytest.raises(BackupError, match="quiescence"):
        create_offline_bundle(
            source, destination, addon_id="demo-addon",
            version="1.0.0", image=IMAGE,
        )
    assert not destination.exists()
    result = create_offline_bundle(
        source, destination, addon_id="demo-addon",
        version="1.0.0", image=IMAGE,
        source_quiesced_acknowledged=True,
    )
    assert result["status"] == "verified_contents_only"
    assert (destination / "database.dump").read_bytes() == source.read_bytes()
    assert result["restore_tested"] is False
    assert result["migration_safe"] is False
    with pytest.raises(BackupError, match="must not exist"):
        create_offline_bundle(
            source, destination, addon_id="demo-addon",
            version="1.0.0", image=IMAGE,
            source_quiesced_acknowledged=True,
        )
    assert source.read_bytes() == b"safe-offline-export"


def test_create_offline_bundle_rejects_symlink_source_and_unsafe_name(tmp_path):
    source = tmp_path / "export.dump"
    source.write_bytes(b"example")
    link = tmp_path / "alias"
    link.symlink_to(source)
    with pytest.raises(BackupError):
        create_offline_bundle(
            link, tmp_path / "out1", addon_id="demo-addon",
            version="1.0.0", image=IMAGE,
            source_quiesced_acknowledged=True,
        )
    with pytest.raises(BackupError, match="Invalid backup file name"):
        create_offline_bundle(
            source, tmp_path / "out2", addon_id="demo-addon",
            version="1.0.0", image=IMAGE, backup_name="../credentials",
            source_quiesced_acknowledged=True,
        )
    assert not (tmp_path / "out1").exists()
    assert not (tmp_path / "out2").exists()


def test_create_offline_bundle_rejects_malformed_source_identity(tmp_path):
    source = tmp_path / "export.dump"
    source.write_bytes(b"example")
    with pytest.raises(ValueError):
        create_offline_bundle(
            source, tmp_path / "out", addon_id="demo-addon",
            version="1.0.0", image="ubuntu:latest",
            source_quiesced_acknowledged=True,
        )
    assert not (tmp_path / "out").exists()


def test_forged_sqlite_consistency_declaration_fails_structure_check(tmp_path):
    root = bundle(tmp_path, quiesced=False)
    (root / "database.dump").rename(root / "database.sqlite3")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"][0]["name"] = "database.sqlite3"
    manifest["consistency_method"] = "sqlite_online_backup"
    path.write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match="SQLite"):
        check(root)
