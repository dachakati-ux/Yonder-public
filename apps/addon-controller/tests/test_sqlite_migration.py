"""SQLite migration contracts run only against restored sandbox backups."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

import pytest
from controller.sqlite_backup import snapshot_sqlite_bundle
from controller.sqlite_migration import (
    MigrationContract,
    MigrationError,
    read_contract,
    rehearse_sqlite_migration,
)
from pydantic import ValidationError

OLD_IMAGE = "ghcr.io/example/demo@sha256:" + "a" * 64
NEW_IMAGE = "ghcr.io/example/demo@sha256:" + "b" * 64


def inputs(tmp_path):
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT)")
        db.execute("INSERT INTO documents (title) VALUES ('unchanged')")
    backup = tmp_path / "backup"
    snapshot_sqlite_bundle(
        source, backup, addon_id="demo-addon",
        version="1.0.0", image=OLD_IMAGE,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(mode=0o700)
    os.chmod(sandbox, 0o700)
    contract = MigrationContract.model_validate({
        "schema_version": 1,
        "addon_id": "demo-addon",
        "source_version": "1.0.0", "source_image": OLD_IMAGE,
        "target_version": "2.0.0", "target_image": NEW_IMAGE,
        "source_db_revision": 0, "target_db_revision": 1,
        "steps": [
            {
                "kind": "add_nullable_column",
                "table": "documents", "column": "owner", "data_type": "TEXT",
            },
            {
                "kind": "create_index",
                "table": "documents", "index_name": "idx_owner",
                "columns": ["owner"],
            },
        ],
    })
    return source, backup, sandbox, contract


def test_rehearses_schema_on_separate_snapshot_without_touching_original(tmp_path):
    source, backup, sandbox, contract = inputs(tmp_path)
    result = rehearse_sqlite_migration(backup, sandbox, contract)
    assert result["sandbox_integrity"] == "passed"
    assert result["steps_rehearsed"] == 2
    assert result["receipt_signed"] is False
    assert len(result["rehearsal_contract_sha256"]) == 64
    assert len(result["source_backup_manifest_sha256"]) == 64
    assert len(result["sandbox_database_sha256"]) == 64
    assert result["row_counts_preserved"] is True
    assert result["foreign_keys_valid"] is True
    assert result["tables_checked"] == 1
    assert result["production_migration_allowed"] is False
    assert result["application_compatibility_verified"] is False
    with sqlite3.connect(source) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "owner" not in {
            row[1] for row in db.execute("PRAGMA table_info(documents)")
        }
        assert db.execute("SELECT title FROM documents").fetchone()[0] == "unchanged"
    with sqlite3.connect(sandbox / "database.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "owner" in {
            row[1] for row in db.execute("PRAGMA table_info(documents)")
        }
        assert db.execute(
            "SELECT title FROM documents"
        ).fetchone()[0] == "unchanged"
    assert sorted(x.name for x in backup.iterdir()) == [
        "database.sqlite3", "manifest.json",
    ]
    assert result["source_backup_manifest_sha256"] == hashlib.sha256(
        (backup / "manifest.json").read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("changes", [
    {"addon_id": "../escape"},
    {"target_version": "1.0.0"},
    {"target_image": OLD_IMAGE},
    {"source_db_revision": 5, "target_db_revision": 2},
    {"steps": [{
        "kind": "add_nullable_column",
        "table": "documents;DROP TABLE documents",
        "column": "safe", "data_type": "TEXT",
    }]},
    {"steps": [{
        "kind": "add_nullable_column",
        "table": "documents", "column": "bad_name",
        "data_type": "TEXT", "sql": "DROP TABLE documents",
    }]},
    {"steps": [{
        "kind": "execute_shell", "command": "rm -rf /",
    }]},
    {"steps": [{
        "kind": "create_index", "table": "documents",
        "index_name": "idx_docs", "columns": ["title", "title"],
    }]},
])
def test_untrusted_contract_fields_fail_closed(tmp_path, changes):
    _, _, _, original = inputs(tmp_path)
    data = original.model_dump()
    data.update(changes)
    with pytest.raises(ValidationError):
        MigrationContract.model_validate(data)


def test_rejects_missing_or_mismatched_database_revision(tmp_path):
    _, backup, sandbox, original = inputs(tmp_path)
    mismatch = original.model_copy(update={"source_db_revision": 5})
    with pytest.raises(MigrationError, match="revision"):
        rehearse_sqlite_migration(backup, sandbox, mismatch)


def test_failed_step_rolls_back_schema_transaction_in_sandbox(tmp_path):
    _, backup, sandbox, original = inputs(tmp_path)
    failing = original.model_copy(update={
        "steps": [
            original.steps[0],
            original.steps[0],
        ]
    })
    with pytest.raises(MigrationError, match="rolled back"):
        rehearse_sqlite_migration(backup, sandbox, failing)
    with sqlite3.connect(sandbox / "database.sqlite3") as db:
        assert "owner" not in {
            row[1] for row in db.execute("PRAGMA table_info(documents)")
        }
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0


def test_migration_cannot_reuse_existing_sandbox_or_modify_backup(tmp_path):
    _, backup, sandbox, contract = inputs(tmp_path)
    (sandbox / "existing").write_text("untouched")
    with pytest.raises(MigrationError, match="empty"):
        rehearse_sqlite_migration(backup, sandbox, contract)
    assert (sandbox / "existing").read_text() == "untouched"


def test_contract_file_requires_private_admin_policy(tmp_path):
    _, _, _, contract = inputs(tmp_path)
    path = tmp_path / "migration.json"
    path.write_text(json.dumps(contract.model_dump()))
    loaded = read_contract(path)
    assert loaded.target_db_revision == 1
    if os.name == "posix":
        os.chmod(path, 0o666)
        with pytest.raises(MigrationError, match="permissions"):
            read_contract(path)


def test_release_binding_rejects_different_backup(tmp_path):
    _, backup, sandbox, original = inputs(tmp_path)
    updated = original.model_copy(update={
        "source_image": "ghcr.io/example/demo@sha256:" + "c" * 64
    })
    with pytest.raises(MigrationError, match="verification"):
        rehearse_sqlite_migration(backup, sandbox, updated)


def test_foreign_key_violations_block_rehearsal_before_migration(tmp_path):
    source = tmp_path / "broken-refs.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT)")
        db.execute("CREATE TABLE refs (id INTEGER PRIMARY KEY, document_id INTEGER "
                   "REFERENCES documents(id))")
        db.execute("INSERT INTO refs(document_id) VALUES (999)")
    from controller.sqlite_backup import snapshot_sqlite_bundle

    bundle = tmp_path / "bundle"
    snapshot_sqlite_bundle(
        source, bundle, addon_id="demo-addon",
        version="1.0.0", image=OLD_IMAGE,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(mode=0o700)
    os.chmod(sandbox, 0o700)
    contract = MigrationContract.model_validate({
        "schema_version": 1,
        "addon_id": "demo-addon",
        "source_version": "1.0.0", "source_image": OLD_IMAGE,
        "target_version": "2.0.0", "target_image": NEW_IMAGE,
        "source_db_revision": 0, "target_db_revision": 1,
        "steps": [{
            "kind": "add_nullable_column", "table": "documents",
            "column": "owner", "data_type": "TEXT",
        }],
    })
    with pytest.raises(MigrationError, match="foreign keys"):
        rehearse_sqlite_migration(bundle, sandbox, contract)
    with sqlite3.connect(source) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0


def test_missing_table_fails_before_begin_migration(tmp_path):
    _, backup, sandbox, original = inputs(tmp_path)
    nonexistent = original.model_copy(update={
        "steps": [
            original.steps[0].model_copy(update={"table": "not_a_table"})
        ]
    })
    with pytest.raises(MigrationError, match="does not exist"):
        rehearse_sqlite_migration(backup, sandbox, nonexistent)
