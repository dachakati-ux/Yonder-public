"""Test administrator-side signing interoperates with offline verifier."""
from __future__ import annotations

import base64
import json
import os
import sys

from controller.migration_trust import verify_migration_attestation
from controller.sqlite_migration import MigrationContract
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sign_migration import main

OLD = "ghcr.io/example/demo@sha256:" + "a" * 64
NEW = "ghcr.io/example/demo@sha256:" + "b" * 64


def test_offline_signing_and_no_overwrite(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes_raw()
    key_file = tmp_path / "seed.key"
    key_file.write_bytes(base64.b64encode(seed))
    if os.name == "posix":
        os.chmod(key_file, 0o600)
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trust = tmp_path / "publisher.json"
    trust.write_text(json.dumps({
        "schema_version": 1,
        "publishers": [{
            "key_id": "reviewed-migration",
            "public_key_ed25519_base64": base64.b64encode(pub).decode(),
        }],
    }))
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"backup":"prior"}')
    contract = MigrationContract(
        schema_version=1, addon_id="demo-addon",
        source_version="1.0.0", source_image=OLD,
        target_version="2.0.0", target_image=NEW,
        source_db_revision=0, target_db_revision=1,
        steps=[{
            "kind": "add_nullable_column",
            "table": "documents", "column": "category", "data_type": "TEXT",
        }],
    )
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract.model_dump()))
    destination = tmp_path / "signed.json"
    monkeypatch.setattr(sys, "argv", [
        "sign_migration.py",
        "--contract", str(contract_path),
        "--backup-manifest", str(manifest),
        "--private-key", str(key_file),
        "--publisher-key-id", "reviewed-migration",
        "--output", str(destination),
    ])
    assert main() == 0
    assert verify_migration_attestation(
        contract, manifest, destination, trust,
    )["status"] == "ed25519_verified"
    assert main() == 1
    assert seed not in destination.read_bytes()
