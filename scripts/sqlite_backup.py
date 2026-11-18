#!/usr/bin/env python3
"""Create, verify, and restore local Yonder SQLite backups safely."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

REQUIRED_TABLES = frozenset(
    {
        "admin_events",
        "conversation_messages",
        "conversations",
        "deployments",
        "model_aliases",
        "model_definitions",
        "request_ledger",
    }
)


def verify_database(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"SQLite database does not exist: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise ValueError(f"SQLite backup is missing required tables: {missing}")
        row_counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in sorted(REQUIRED_TABLES)
        }
    return {
        "status": "verified",
        "path": str(path.resolve()),
        "integrity": "ok",
        "tables": sorted(REQUIRED_TABLES),
        "row_counts": row_counts,
    }


def create_backup(source: Path, output: Path, *, force: bool = False) -> dict[str, object]:
    verify_database(source)
    if output.exists() and not force:
        raise ValueError(f"Backup destination already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(output) as backup_db:
        source_db.backup(backup_db)
    return verify_database(output)


def restore_backup(backup: Path, output: Path, *, force: bool = False) -> dict[str, object]:
    verify_database(backup)
    if output.exists() and not force:
        raise ValueError(f"Restore destination already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, output)
    return verify_database(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("database", type=Path)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("source", type=Path)
    backup_parser.add_argument("output", type=Path)
    backup_parser.add_argument("--force", action="store_true")

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("output", type=Path)
    restore_parser.add_argument("--force", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "verify":
            result = verify_database(args.database)
        elif args.command == "backup":
            result = create_backup(args.source, args.output, force=args.force)
        else:
            result = restore_backup(args.backup, args.output, force=args.force)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"SQLite backup operation failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
