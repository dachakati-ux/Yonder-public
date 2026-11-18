"""Declarative SQLite migration contract and *offline-only* rehearsal.

No raw publisher SQL, shell commands, Docker access or live database mutations.
The deliberately narrow v1 contract supports adding nullable columns and
indexes in a disposable restored backup. Production migration execution is
NOT implemented.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .backup_verifier import BackupError, verify_restore_into_empty_directory

IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[a-z0-9._-]+)?@sha256:[a-f0-9]{64}$")
ADDON = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
MAX_CONTRACT = 65536


class MigrationError(ValueError):
    """Migration details are deliberately safe for admin diagnostics."""


class AddColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["add_nullable_column"]
    table: str
    column: str
    data_type: Literal["TEXT", "INTEGER", "REAL", "BLOB"]


class CreateIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["create_index"]
    table: str
    index_name: str
    columns: list[str] = Field(min_length=1, max_length=8)


Step = AddColumn | CreateIndex


class MigrationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    addon_id: str
    source_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    source_image: str
    target_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    target_image: str
    source_db_revision: int = Field(ge=0, le=1_000_000)
    target_db_revision: int = Field(ge=1, le=1_000_000)
    steps: list[Step] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def safe(self) -> MigrationContract:
        if not ADDON.fullmatch(self.addon_id):
            raise ValueError("Invalid migration add-on identity")
        if not _IMAGE.fullmatch(self.source_image) or not _IMAGE.fullmatch(
            self.target_image
        ):
            raise ValueError("Migration requires pinned OCI images")
        if (
            self.source_version == self.target_version
            or self.source_image == self.target_image
            or self.target_db_revision <= self.source_db_revision
        ):
            raise ValueError("Migration must advance release and database revisions")
        for step in self.steps:
            names = (
                [step.table, step.column]
                if isinstance(step, AddColumn)
                else [step.table, step.index_name, *step.columns]
            )
            if any(not IDENTIFIER.fullmatch(item) for item in names):
                raise ValueError("Invalid migration identifier")
            if isinstance(step, CreateIndex) and len(set(step.columns)) != len(
                step.columns
            ):
                raise ValueError("Duplicate index columns")
        return self


def read_contract(path: Path) -> MigrationContract:
    if path.is_symlink() or not path.is_file():
        raise MigrationError("Reviewed migration contract not available")
    if path.stat().st_size > MAX_CONTRACT:
        raise MigrationError("Migration contract exceeds maximum size")
    if os.name == "posix" and path.stat().st_mode & 0o022:
        raise MigrationError("Migration contract permissions are unsafe")
    try:
        return MigrationContract.model_validate(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError) as exc:
        raise MigrationError("Invalid reviewed migration contract") from exc


def _quoted(value: str) -> str:
    # Identifiers have been validated by the Pydantic contract.
    return '"' + value + '"'


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute("PRAGMA table_info(" + _quoted(table) + ")").fetchall()
    if not rows:
        raise MigrationError("Migration table does not exist")
    return {row[1] for row in rows}


def _apply_step(connection: sqlite3.Connection, step: Step) -> None:
    existing = _columns(connection, step.table)
    if isinstance(step, AddColumn):
        if step.column in existing:
            raise MigrationError("Migration column already exists")
        connection.execute(
            "ALTER TABLE " + _quoted(step.table) + " ADD COLUMN "
            + _quoted(step.column) + " " + step.data_type
        )
    else:
        if any(col not in existing for col in step.columns):
            raise MigrationError("Index references unavailable columns")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (step.index_name,)
        ).fetchone():
            raise MigrationError("Migration index already exists")
        connection.execute(
            "CREATE INDEX " + _quoted(step.index_name) + " ON "
            + _quoted(step.table) + "("
            + ",".join(_quoted(name) for name in step.columns) + ")"
        )


def rehearse_sqlite_migration(
    bundle: Path, sandbox: Path, contract: MigrationContract
) -> dict:
    """Run a reversible-schema rehearsal in an *empty* sandbox directory.

    Only migrated sandbox bytes change. The original bundle remains untouched.
    A passing rehearsal never authorizes production upgrades or downgrades.
    """
    if sandbox.resolve() == bundle.resolve():
        raise MigrationError("Cannot migrate the backup bundle itself")
    if sandbox.is_symlink() or not sandbox.is_dir():
        raise MigrationError("Sandbox must already be a private empty directory")
    if os.name == "posix" and sandbox.stat().st_mode & 0o077:
        raise MigrationError("Sandbox directory permissions are unsafe")
    if any(sandbox.iterdir()):
        raise MigrationError("Sandbox must be empty before migration rehearsal")
    try:
        verified = verify_restore_into_empty_directory(
            bundle, sandbox,
            addon_id=contract.addon_id,
            version=contract.source_version,
            image=contract.source_image,
        )
    except BackupError as exc:
        raise MigrationError("Previous release backup verification failed") from exc
    db_path = sandbox / "database.sqlite3"
    if not db_path.is_file() or db_path.is_symlink():
        raise MigrationError("A validated SQLite snapshot is required")
    if verified.get("consistency_method") != "sqlite_online_backup":
        raise MigrationError("Only SQLite-native backup bundles support rehearsal")

    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("PRAGMA foreign_keys=ON")
        old_revision = db.execute("PRAGMA user_version").fetchone()[0]
        if old_revision != contract.source_db_revision:
            raise MigrationError("Backup database revision does not match contract")
        baseline = db.execute("PRAGMA integrity_check").fetchone()
        if baseline is None or baseline[0] != "ok":
            raise MigrationError("Restored database failed integrity check")
        # BEGIN explicitly: all permitted SQLite DDL is transactional.
        try:
            db.execute("BEGIN IMMEDIATE")
            for step in contract.steps:
                _apply_step(db, step)
            db.execute("PRAGMA user_version=" + str(contract.target_db_revision))
            after = db.execute("PRAGMA integrity_check").fetchone()
            if after is None or after[0] != "ok":
                raise MigrationError("Migrated database failed integrity check")
            db.commit()
        except (sqlite3.Error, MigrationError) as exc:
            db.rollback()
            raise MigrationError("Sandbox migration failed and transaction rolled back") from exc
        actual_revision = db.execute("PRAGMA user_version").fetchone()[0]
        if actual_revision != contract.target_db_revision:
            raise MigrationError("Result database revision does not match contract")
    return {
        "addon_id": contract.addon_id,
        "from_version": contract.source_version,
        "to_version": contract.target_version,
        "source_db_revision": contract.source_db_revision,
        "target_db_revision": contract.target_db_revision,
        "steps_rehearsed": len(contract.steps),
        "backup_integrity": verified["status"],
        "sandbox_integrity": "passed",
        "production_migration_allowed": False,
        "application_compatibility_verified": False,
        "downgrade_tested": False,
    }
