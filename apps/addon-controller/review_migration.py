"""Offline operator review of durable migration rehearsal journal state."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from controller.migration_journal import JournalError, MigrationJournal


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review or acknowledge a manually inspected offline rehearsal"
    )
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument(
        "--acknowledge-interrupted", action="store_true",
        help="Clear a failed/interrupted sandbox review block; NEVER mark passed",
    )
    args = parser.parse_args()
    try:
        journal = MigrationJournal(args.journal)
        record = journal.get(args.id)
        if record is None:
            raise JournalError("Migration rehearsal ID not found")
        if args.acknowledge_interrupted:
            journal.acknowledge_interrupted(args.id)
            record = journal.get(args.id)
    except (JournalError, OSError, ValueError) as exc:
        print("Migration review rejected:", str(exc), file=sys.stderr)
        return 1
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
