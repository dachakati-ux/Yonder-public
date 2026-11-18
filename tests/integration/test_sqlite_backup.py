"""SQLite backup and restore verification tests."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "sqlite_backup", ROOT / "scripts" / "sqlite_backup.py"
)
assert SPEC is not None and SPEC.loader is not None
sqlite_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sqlite_backup)


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        for table in sqlite_backup.REQUIRED_TABLES:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.execute("INSERT INTO deployments DEFAULT VALUES")
        connection.commit()


def test_backup_verify_and_restore_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    _create_database(source)

    backup_result = sqlite_backup.create_backup(source, backup)
    restore_result = sqlite_backup.restore_backup(backup, restored)

    assert backup_result["integrity"] == "ok"
    assert restore_result["row_counts"]["deployments"] == 1


def test_backup_refuses_incomplete_schema(tmp_path: Path) -> None:
    database = tmp_path / "incomplete.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE deployments (id INTEGER PRIMARY KEY)")

    with pytest.raises(ValueError, match="missing required tables"):
        sqlite_backup.verify_database(database)
