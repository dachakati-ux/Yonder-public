"""Offline-only migration rehearsal of an already verified SQLite backup."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from controller.sqlite_migration import (
    MigrationError,
    read_contract,
    rehearse_sqlite_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse a reviewed SQLite migration on a fresh restored backup"
    )
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--sandbox", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = read_contract(args.contract)
        result = rehearse_sqlite_migration(args.backup, args.sandbox, contract)
    except (MigrationError, OSError, ValueError) as exc:
        print("Migration rehearsal rejected:", str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    print(
        "Sandbox-only result; this does NOT authorize a live database migration.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
