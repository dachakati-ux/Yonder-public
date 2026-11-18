"""Offline-only credential administration. No secrets in argv, URLs or API bodies."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from controller.credential_vault import (
    CredentialVault,
    VaultError,
    create_master_key,
)

DEFAULT_STORE = "/var/lib/yonder/credentials.db"
DEFAULT_KEY = "/etc/yonder/credentials.key"


def main() -> int:
    parser = argparse.ArgumentParser(description="Yonder local credential inventory")
    parser.add_argument("--database", type=Path, default=Path(DEFAULT_STORE))
    parser.add_argument("--key-file", type=Path, default=Path(DEFAULT_KEY))
    operations = parser.add_subparsers(dest="action", required=True)
    operations.add_parser("init-key")
    for name in ("set", "revoke", "status"):
        command = operations.add_parser(name)
        command.add_argument("addon_id")
        if name != "status":
            command.add_argument("credential_name")
    args = parser.parse_args()
    try:
        if args.action == "init-key":
            create_master_key(args.key_file)
            print("Master key created. Back it up to a secure offline location.")
            return 0
        vault = CredentialVault(args.database, args.key_file)
        if args.action == "set":
            first = getpass.getpass("Credential value (hidden): ")
            second = getpass.getpass("Confirm credential value: ")
            if first != second:
                raise VaultError("Credential values do not match")
            if not first:
                raise VaultError("Empty credentials are not permitted")
            revision = vault.put(
                args.addon_id, args.credential_name, first.encode("utf-8")
            )
            print("Credential stored encrypted. Revision:", revision)
        elif args.action == "revoke":
            revision = vault.revoke(args.addon_id, args.credential_name)
            print("Credential revoked. Revision:", revision)
        else:
            for entry in vault.metadata(args.addon_id):
                print(
                    entry["name"], "configured" if entry["configured"] else "revoked",
                    "revision", entry["revision"],
                )
        return 0
    except (VaultError, OSError, UnicodeError) as exc:
        print("Credential operation rejected:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
