"""Offline-only credential administration. No secrets in argv, URLs or API bodies."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from controller.credential_delivery import DeliveryError, deliver, remove_delivered
from controller.credential_vault import (
    CredentialVault,
    VaultError,
    create_master_key,
)
from controller.engine import ControllerError, load_deployments

DEFAULT_STORE = "/var/lib/yonder/credentials.db"
DEFAULT_KEY = "/etc/yonder/credentials.key"


def main() -> int:
    parser = argparse.ArgumentParser(description="Yonder local credential inventory")
    parser.add_argument("--database", type=Path, default=Path(DEFAULT_STORE))
    parser.add_argument("--key-file", type=Path, default=Path(DEFAULT_KEY))
    parser.add_argument(
        "--approvals", type=Path, default=Path("/etc/yonder/deployments.json")
    )
    operations = parser.add_subparsers(dest="action", required=True)
    operations.add_parser("init-key")
    rotate_cmd = operations.add_parser("rotate-key")
    rotate_cmd.add_argument("new_key_path", type=Path)
    backup_cmd = operations.add_parser("backup")
    backup_cmd.add_argument("destination", type=Path)
    for name in ("set", "revoke", "status", "audit", "deliver", "remove-delivered"):
        command = operations.add_parser(name)
        command.add_argument("addon_id")
        if name in ("set", "revoke", "deliver", "remove-delivered"):
            command.add_argument("credential_name")
    args = parser.parse_args()
    try:
        if args.action == "init-key":
            create_master_key(args.key_file)
            print("Master key created. Back it up to a secure offline location.")
            return 0
        vault = CredentialVault(args.database, args.key_file)
        if args.action == "rotate-key":
            count = vault.rotate_master_key(args.new_key_path)
            print(
                f"Master key rotated ({count} record(s) re-encrypted). "
                f"New key at: {args.new_key_path}"
            )
            print("Update YONDER_CONTROLLER_CREDENTIAL_KEY and back up the new key offline.")
        elif args.action == "backup":
            vault.backup(args.destination)
            print("Encrypted records backed up to:", args.destination)
            print("Back up the master key separately; it is not in this file.")
        elif args.action == "set":
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
        elif args.action in ("deliver", "remove-delivered"):
            # Explicit local operator invocation, never an HTTP request.
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                raise DeliveryError("Docker credential delivery requires local host admin")
            approvals = load_deployments(args.approvals)
            approved = {
                name for name, config in approvals.items() if config.secret_volume
            }
            policy = approvals.get(args.addon_id)
            if (
                policy is None or not policy.secret_volume
                or args.credential_name not in policy.credential_names
            ):
                raise DeliveryError("Credential name is not declared for this add-on")
            if args.action == "deliver":
                deliver(
                    vault, args.addon_id, args.credential_name,
                    approved_addon_ids=approved,
                )
                print("Credential delivered to approved local Docker volume.")
                print("Restart or reload the add-on if it does not watch credential files.")
            else:
                remove_delivered(
                    vault, args.addon_id, args.credential_name,
                    approved_addon_ids=approved,
                )
                print("Delivered file removed; revoke provider-side credentials separately.")
        elif args.action == "audit":
            for event in vault.audit_events(args.addon_id):
                print(
                    event["timestamp"], event["action"], event["credential_name"],
                    "revision", event["revision"], "uid", event["actor_uid"],
                )
        else:
            for entry in vault.metadata(args.addon_id):
                delivery = entry.get("delivery") or {}
                print(
                    entry["name"],
                    "configured" if entry["configured"] else "revoked",
                    "revision", entry["revision"],
                    f"delivery={delivery.get('status', 'unknown')}",
                    f"delivered_revision={delivery.get('delivered_revision')}",
                )
        return 0
    except (VaultError, DeliveryError, ControllerError, OSError, UnicodeError) as exc:
        print("Credential operation rejected:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
