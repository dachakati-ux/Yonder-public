"""Offline-only migration rehearsal of an already verified SQLite backup."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from controller.migration_journal import JournalError, MigrationJournal
from controller.migration_trust import (
    MigrationTrustError,
    verify_migration_attestation,
)
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
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--publisher-trust", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = read_contract(args.contract)
        verified = verify_migration_attestation(
            contract, args.backup / "manifest.json",
            args.attestation, args.publisher_trust,
        )
        journal = MigrationJournal(args.journal)
        entry = journal.start(
            contract.addon_id, contract.source_image,
            contract.target_image, verified["contract_sha256"],
            verified["backup_manifest_sha256"],
        )
        try:
            result = rehearse_sqlite_migration(
                args.backup, args.sandbox, contract
            )
        except Exception:
            journal.finish(entry, "failed")
            raise
        journal.finish(
            entry, "passed",
            sandbox_database_sha256=result["sandbox_database_sha256"],
        )
        result["rehearsal_id"] = entry
        result["publisher_verification"] = verified["status"]
        result["journal_state"] = "passed"
    except (MigrationError, MigrationTrustError, JournalError, OSError, ValueError) as exc:
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
