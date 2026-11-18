"""Offline backup verification; never modifies running add-ons or Docker volumes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from controller.backup_verifier import (
    BackupError,
    verify_bundle,
    verify_restore_into_empty_directory,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an offline quiesced backup before reviewing migration"
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--addon-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--restore-sandbox", type=Path,
        help="An independently prepared EMPTY private directory, never live data",
    )
    args = parser.parse_args()
    try:
        if args.restore_sandbox:
            result = verify_restore_into_empty_directory(
                args.source, args.restore_sandbox,
                addon_id=args.addon_id, version=args.version, image=args.image,
            )
        else:
            result = verify_bundle(
                args.source,
                addon_id=args.addon_id, version=args.version, image=args.image,
            )
    except (BackupError, OSError) as exc:
        print("Backup verification failed:", str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
