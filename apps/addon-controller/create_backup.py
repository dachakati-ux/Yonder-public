"""Create an offline backup bundle from a separately quiesced database export.

This is intentionally not a live Docker volume snapshot or a DB-specific
backup runner. Operators must establish transactionally consistent exports.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from controller.backup_verifier import BackupError, create_offline_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Package an already-quiesced add-on export")
    parser.add_argument("--source-file", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--addon-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--backup-name", default="database.dump")
    parser.add_argument(
        "--acknowledge-source-quiesced", required=True, action="store_true",
        help="Affirm the database was stopped or exported transactionally",
    )
    args = parser.parse_args()
    try:
        result = create_offline_bundle(
            args.source_file, args.output_directory,
            addon_id=args.addon_id, version=args.version, image=args.image,
            backup_name=args.backup_name,
            source_quiesced_acknowledged=args.acknowledge_source_quiesced,
        )
    except (BackupError, OSError, ValueError) as exc:
        print("Backup packaging rejected:", str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    print(
        "Backup contents verified; application-level consistency and restore "
        "remain unproven.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
