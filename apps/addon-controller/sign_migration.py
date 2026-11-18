"""Offline signing of a reviewed migration contract and backup-manifest digest.

Only a local administrator with the private key can create this attestation.
The private key is never imported by controller APIs or committed to Git.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from controller.migration_trust import (
    MigrationAttestation,
    canonical_contract_hash,
)
from controller.sqlite_migration import read_contract


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sign a reviewed SQLite migration and its exact backup manifest"
    )
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--publisher-key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if (
            args.private_key.is_symlink()
            or not args.private_key.is_file()
            or (os.name == "posix" and args.private_key.stat().st_mode & 0o077)
        ):
            raise ValueError("Publisher private key requires private file permissions")
        if args.backup_manifest.is_symlink() or not args.backup_manifest.is_file():
            raise ValueError("Backup manifest is unavailable or unsafe")
        if args.backup_manifest.stat().st_size > 2_000_000:
            raise ValueError("Backup manifest exceeds policy limit")
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Attestation output already exists")
        encoded = args.private_key.read_bytes().strip()
        raw_key = base64.b64decode(encoded, validate=True)
        if len(raw_key) != 32:
            raise ValueError("Expected a base64-encoded 32-byte Ed25519 seed")
        private_key = Ed25519PrivateKey.from_private_bytes(raw_key)
        contract = read_contract(args.contract)
        attestation = MigrationAttestation(
            schema_version=1,
            addon_id=contract.addon_id,
            source_version=contract.source_version,
            source_image=contract.source_image,
            target_version=contract.target_version,
            target_image=contract.target_image,
            contract_sha256=canonical_contract_hash(contract),
            backup_manifest_sha256=hashlib.sha256(
                args.backup_manifest.read_bytes()
            ).hexdigest(),
            publisher_key_id=args.publisher_key_id,
            signature_ed25519_base64=base64.b64encode(b"0" * 64).decode(),
        )
        signed = attestation.model_copy(update={
            "signature_ed25519_base64": base64.b64encode(
                private_key.sign(attestation.signing_bytes())
            ).decode(),
        })
        # Create exclusively, no overwrite or path supplied by the browser.
        fd = os.open(
            args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(signed.model_dump(), output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        print("Signed migration attestation created.")
        return 0
    except (OSError, ValueError) as exc:
        print("Migration signing failed:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
