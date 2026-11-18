"""SQLite-native backup tests: WAL consistency and offline restore."""
from __future__ import annotations

import os
import sqlite3

import pytest
from controller.backup_verifier import (
    BackupError,
    verify_bundle,
    verify_restore_into_empty_directory,
)
from controller.sqlite_backup import snapshot_sqlite_bundle

IMAGE = "ghcr.io/example/sqlite-addon@sha256:" + "a" * 64


def source_database(tmp_path):
    source = tmp_path / "original.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT)")
        db.executemany(
            "INSERT INTO documents(title) VALUES (?)",
            [("first",), ("second",), ("third",)],
        )
    return source


def test_native_snapshot_handles_wal_and_is_restorable(tmp_path):
    source = source_database(tmp_path)
    output = tmp_path / "sqlite-backup"
    result = snapshot_sqlite_bundle(
        source, output, addon_id="sqlite-addon",
        version="1.0.0", image=IMAGE,
    )
    assert result["status"] == "verified_contents_only"
    assert result["consistency_method"] == "sqlite_online_backup"
    assert result["source_quiesced_declared"] is False
    assert result["migration_safe"] is False
    verified = verify_bundle(
        output, addon_id="sqlite-addon", version="1.0.0", image=IMAGE,
    )
    assert verified["file_count"] == 1
    # Verification must be read-only, not create WAL/SHM files in a sealed bundle.
    assert sorted(item.name for item in output.iterdir()) == [
        "database.sqlite3", "manifest.json",
    ]
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(mode=0o700)
    os.chmod(sandbox, 0o700)
    restored = verify_restore_into_empty_directory(
        output, sandbox,
        addon_id="sqlite-addon", version="1.0.0", image=IMAGE,
    )
    assert restored["restore_tested"] is True
    with sqlite3.connect(sandbox / "database.sqlite3") as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        rows = db.execute("SELECT title FROM documents ORDER BY id").fetchall()
    assert rows == [("first",), ("second",), ("third",)]
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3


def test_native_snapshot_does_not_accept_broken_sqlite(tmp_path):
    source = tmp_path / "not-db.db"
    source.write_bytes(b"malformed sqlite data")
    output = tmp_path / "not-created"
    with pytest.raises(BackupError, match="SQLite"):
        snapshot_sqlite_bundle(
            source, output, addon_id="sqlite-addon",
            version="1.0.0", image=IMAGE,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".yonder-sqlite-snapshot-*"))


def test_native_snapshot_denies_symlink_and_existing_destination(tmp_path):
    source = source_database(tmp_path)
    link = tmp_path / "symlink"
    link.symlink_to(source)
    with pytest.raises(BackupError, match="non-symlink"):
        snapshot_sqlite_bundle(
            link, tmp_path / "blocked",
            addon_id="sqlite-addon", version="1.0.0", image=IMAGE,
        )
    existing = tmp_path / "occupied"
    existing.mkdir()
    with pytest.raises(BackupError, match="must not exist"):
        snapshot_sqlite_bundle(
            source, existing,
            addon_id="sqlite-addon", version="1.0.0", image=IMAGE,
        )


def test_native_snapshot_rejects_untrusted_parent(tmp_path):
    source = source_database(tmp_path)
    parent = tmp_path / "public"
    parent.mkdir()
    os.chmod(parent, 0o777)
    if os.name == "posix":
        with pytest.raises(BackupError, match="group/other writable"):
            snapshot_sqlite_bundle(
                source, parent / "backup",
                addon_id="sqlite-addon", version="1.0.0", image=IMAGE,
            )
