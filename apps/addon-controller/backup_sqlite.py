"""Host-local SQLite online backup; never modifies the original database."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from controller.backup_verifier import BackupError
from controller.sqlite_backup import snapshot_sqlite_bundle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a consistent SQLite backup bundle for a reviewed add-on"
    )
    parser.add_argument("--source-database", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--addon-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        result = snapshot_sqlite_bundle(
            args.source_database, args.output_directory,
            addon_id=args.addon_id, version=args.version, image=args.image,
        )
    except (BackupError, OSError, ValueError) as exc:
        print("SQLite backup rejected:", str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    print(
        "SQLite bytes/structure verified; application-level restore and "
        "migration safety are not yet verified.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
