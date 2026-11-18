"""Signed offline migration attestations and durable restart-safe rehearsal journal."""
from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest
from controller.migration_journal import JournalError, MigrationJournal
from controller.migration_trust import (
    MigrationAttestation,
    MigrationTrustError,
    canonical_contract_hash,
    verify_migration_attestation,
)
from controller.sqlite_migration import MigrationContract
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

OLD = "ghcr.io/example/addon@sha256:" + "a" * 64
NEW = "ghcr.io/example/addon@sha256:" + "b" * 64


def setup_signed(tmp_path):
    contract = MigrationContract.model_validate({
        "schema_version": 1, "addon_id": "demo-addon",
        "source_version": "1.0.0", "source_image": OLD,
        "target_version": "2.0.0", "target_image": NEW,
        "source_db_revision": 0, "target_db_revision": 1,
        "steps": [{
            "kind": "add_nullable_column", "table": "documents",
            "column": "owner", "data_type": "TEXT",
        }],
    })
    backup = tmp_path / "manifest.json"
    backup.write_text('{"source":"sample"}')
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trust = tmp_path / "trust.json"
    trust.write_text(json.dumps({
        "schema_version": 1,
        "publishers": [{
            "key_id": "official-migration",
            "public_key_ed25519_base64": base64.b64encode(pub).decode(),
        }],
    }))
    unsigned = MigrationAttestation(
        schema_version=1,
        addon_id=contract.addon_id,
        source_version=contract.source_version,
        source_image=contract.source_image,
        target_version=contract.target_version,
        target_image=contract.target_image,
        contract_sha256=canonical_contract_hash(contract),
        backup_manifest_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
        publisher_key_id="official-migration",
        signature_ed25519_base64=base64.b64encode(b"0" * 64).decode(),
    )
    signed = unsigned.model_copy(update={
        "signature_ed25519_base64": base64.b64encode(
            key.sign(unsigned.signing_bytes())
        ).decode(),
    })
    attestation = tmp_path / "migration.attestation.json"
    attestation.write_text(json.dumps(signed.model_dump()))
    return contract, backup, trust, attestation


def test_signed_migration_and_backup_are_bound(tmp_path):
    contract, backup, trust, attestation = setup_signed(tmp_path)
    verification = verify_migration_attestation(
        contract, backup, attestation, trust
    )
    assert verification["status"] == "ed25519_verified"
    assert verification["publisher_key_id"] == "official-migration"
    assert verification["production_migration_allowed"] == "false"
    backup.write_text("tampered")
    with pytest.raises(MigrationTrustError, match="backup"):
        verify_migration_attestation(contract, backup, attestation, trust)


def test_tampered_migration_contract_and_bad_signature_are_denied(tmp_path):
    contract, backup, trust, attestation = setup_signed(tmp_path)
    altered = contract.model_copy(update={"target_db_revision": 2})
    with pytest.raises(MigrationTrustError, match="contract"):
        verify_migration_attestation(altered, backup, attestation, trust)
    data = json.loads(attestation.read_text())
    data["signature_ed25519_base64"] = base64.b64encode(b"1" * 64).decode()
    attestation.write_text(json.dumps(data))
    with pytest.raises(MigrationTrustError, match="signature"):
        verify_migration_attestation(contract, backup, attestation, trust)


@pytest.mark.parametrize("policy", [
    {"revoked": True},
    {"not_after_utc": "2020-01-01T00:00:00+00:00"},
])
def test_revoked_or_expired_publisher_cannot_sign_migrations(tmp_path, policy):
    contract, backup, trust, attestation = setup_signed(tmp_path)
    payload = json.loads(trust.read_text())
    payload["publishers"][0].update(policy)
    trust.write_text(json.dumps(payload))
    with pytest.raises(MigrationTrustError):
        verify_migration_attestation(contract, backup, attestation, trust)


def test_journal_records_evidence_and_cannot_overwrite_completion(tmp_path):
    journal = MigrationJournal(tmp_path / "journal.db")
    run = journal.start("demo-addon", OLD, NEW, "a" * 64, "b" * 64)
    with pytest.raises(JournalError, match="Unresolved"):
        journal.start("demo-addon", OLD, NEW, "a" * 64, "b" * 64)
    journal.finish(run, "passed", sandbox_database_sha256="c" * 64)
    result = journal.get(run)
    assert result["state"] == "passed"
    assert result["contract_sha256"] == "a" * 64
    assert result["sandbox_database_sha256"] == "c" * 64
    with pytest.raises(JournalError, match="not active"):
        journal.finish(run, "failed")
    again = journal.start("demo-addon", OLD, NEW, "a" * 64, "b" * 64)
    journal.finish(again, "failed")
    assert journal.get(again)["state"] == "failed"


def test_restarted_journal_requires_manual_acknowledgment(tmp_path):
    location = tmp_path / "journal.db"
    journal = MigrationJournal(location)
    run = journal.start("demo-addon", OLD, NEW, "a" * 64, "b" * 64)
    resumed = MigrationJournal(location)
    assert resumed.get(run)["state"] == "interrupted"
    with pytest.raises(JournalError, match="Unresolved"):
        resumed.start("demo-addon", OLD, NEW, "a" * 64, "b" * 64)
    resumed.acknowledge_interrupted(run)
    assert resumed.get(run)["state"] == "acknowledged"
    assert resumed.start("demo-addon", OLD, NEW, "a" * 64, "b" * 64)


def test_private_journal_permissions_and_symlink_rejected(tmp_path):
    journal_path = tmp_path / "rehearsals.db"
    MigrationJournal(journal_path)
    if os.name == "posix":
        assert journal_path.stat().st_mode & 0o077 == 0
        os.chmod(journal_path, 0o644)
        with pytest.raises(JournalError, match="private"):
            MigrationJournal(journal_path)
    alias = tmp_path / "alias.db"
    alias.symlink_to(journal_path)
    with pytest.raises(JournalError, match="symlink"):
        MigrationJournal(alias)


def test_journal_never_marks_passed_without_a_verified_output(tmp_path):
    journal = MigrationJournal(tmp_path / "journal.db")
    run = journal.start("demo-addon", OLD, NEW, "a" * 64, "b" * 64)
    with pytest.raises(JournalError, match="checksum"):
        journal.finish(run, "passed")
    assert journal.get(run)["state"] == "running"
