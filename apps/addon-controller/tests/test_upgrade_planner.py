"""Read-only upgrade assessments must not execute Docker or mutate data."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime

import pytest
from controller.engine import Controller
from controller.migration_trust import MigrationAttestation, canonical_contract_hash
from controller.models import ApprovedDeployment
from controller.sqlite_migration import MigrationContract
from controller.store import JobConflict
from controller.upgrade_planner import UpgradePolicy, assess_upgrade
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

IMAGE = "ghcr.io/example/demo@sha256:" + "a" * 64
NEXT_IMAGE = "ghcr.io/example/demo@sha256:" + "b" * 64


def deployment(*, persistent=False):
    return ApprovedDeployment(
        addon_id="demo-addon", version="2.0.0", image=NEXT_IMAGE,
        data_volume=persistent,
    )


@pytest.mark.parametrize("observed", ["running", "stopped", "not_deployed"])
def test_upgrade_is_never_executable_without_rollback(observed):
    original = {
        "addon_id": "demo-addon", "version": "1.0.0",
        "image": IMAGE, "desired_state": "running",
    }
    result = assess_upgrade(
        original, observed, deployment(),
        UpgradePolicy(data_migration="none"),
    )
    assert result["can_execute"] is False
    assert result["rollback_supported"] is False
    assert any(row["name"] == "rollback" for row in result["checks"])
    if observed != "running":
        assert any(
            row["name"] == "observed_state" and row["status"] == "blocked"
            for row in result["checks"]
        )


def test_persistent_data_upgrade_is_blocked_with_explicit_migration_warning():
    result = assess_upgrade(
        {
            "version": "1.0.0", "image": IMAGE,
            "desired_state": "running",
        },
        "running",
        deployment(persistent=True),
        UpgradePolicy(data_migration="requires_migration"),
    )
    assert result["can_execute"] is False
    assert {row["name"] for row in result["checks"] if row["status"] == "blocked"} >= {
        "persistent_data", "migration", "rollback",
    }
    assert "backup" in str(result["checks"]).lower()


def test_migration_without_backup_is_invalid():
    with pytest.raises(ValidationError):
        UpgradePolicy(data_migration="requires_migration", backup_required=False)


class FakeDocker:
    def __init__(self):
        self.calls = []

    async def status(self, addon_id):
        self.calls.append(("status", addon_id))
        return "running"


@pytest.mark.asyncio
async def test_controller_upgrade_preview_only_inspects_running_container(tmp_path):
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [{
            "addon_id": "demo-addon",
            "version": "2.0.0",
            "image": NEXT_IMAGE,
            "data_volume": True,
        }],
    }))
    adapter = FakeDocker()
    controller = Controller(approved, tmp_path / "jobs.db", adapter=adapter)
    controller.jobs.set_desired("demo-addon", "1.0.0", IMAGE, "running")
    preview = await controller.upgrade_assessment("demo-addon")
    assert preview["from"]["version"] == "1.0.0"
    assert preview["to"]["version"] == "2.0.0"
    assert preview["can_execute"] is False
    assert adapter.calls == [("status", "demo-addon")]
    assert controller.jobs.desired()[0]["version"] == "1.0.0"
    assert controller.jobs.list() == []


def test_unknown_migration_policy_blocks_without_guessing():
    result = assess_upgrade(
        {
            "version": "1.0.0",
            "image": IMAGE,
            "desired_state": "running",
        },
        "running",
        deployment(persistent=True),
        UpgradePolicy(),
    )
    assert result["data_migration"] == "unknown"
    assert result["can_execute"] is False
    assert any(
        row["name"] == "migration" and row["status"] == "blocked"
        and "no reviewed" in row["explanation"]
        for row in result["checks"]
    )


@pytest.mark.asyncio
async def test_stateful_upgrade_reports_matching_migration_contract_but_stays_blocked(
    tmp_path, monkeypatch
):
    backups = tmp_path / "offline-backups"
    root = backups / "demo-addon"
    root.mkdir(parents=True, mode=0o700)
    db_bytes = b"offline export"
    (root / "database.dump").write_bytes(db_bytes)
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "addon_id": "demo-addon",
        "version": "1.0.0", "image": IMAGE,
        "created_at": datetime.now(UTC).isoformat(),
        "source_quiesced": True, "backup_kind": "database_export",
        "files": [{
            "name": "database.dump", "size": len(db_bytes),
            "sha256": hashlib.sha256(db_bytes).hexdigest(),
        }],
    }))
    contracts = tmp_path / "migration-contracts"
    contracts.mkdir()
    migration_path = contracts / "demo-addon.json"
    migration = {
        "schema_version": 1, "addon_id": "demo-addon",
        "source_version": "1.0.0", "source_image": IMAGE,
        "target_version": "2.0.0", "target_image": NEXT_IMAGE,
        "source_db_revision": 0, "target_db_revision": 1,
        "steps": [{
            "kind": "add_nullable_column", "table": "documents",
            "column": "owner", "data_type": "TEXT",
        }],
    }
    migration_path.write_text(json.dumps(migration))
    monkeypatch.setenv("YONDER_CONTROLLER_STATEFUL_BACKUP_ROOT", str(backups))
    monkeypatch.setenv("YONDER_CONTROLLER_MIGRATION_ROOT", str(contracts))
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps({
        "schema_version": 1, "deployments": [{
            "addon_id": "demo-addon", "version": "2.0.0",
            "image": NEXT_IMAGE, "data_volume": True,
            "upgrade_data_migration": "requires_migration",
        }],
    }))
    engine = Controller(approved, tmp_path / "jobs.db", adapter=FakeDocker())
    engine.jobs.set_desired("demo-addon", "1.0.0", IMAGE, "running")
    report = await engine.upgrade_assessment("demo-addon")
    assert report["backup_integrity"] == "verified_contents_only"
    assert report["migration_contract"] == "identity_verified_only"
    assert report["migration_signature"] == "not_verified"
    assert report["can_execute"] is False

    signing_key = Ed25519PrivateKey.generate()
    pub = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trust = tmp_path / "publishers.json"
    trust.write_text(json.dumps({
        "schema_version": 1,
        "publishers": [{
            "key_id": "migration-publisher",
            "public_key_ed25519_base64": base64.b64encode(pub).decode(),
        }],
    }))
    engine.publisher_trust = trust
    contract = MigrationContract.model_validate(migration)
    unsigned = MigrationAttestation(
        schema_version=1, addon_id="demo-addon",
        source_version="1.0.0", source_image=IMAGE,
        target_version="2.0.0", target_image=NEXT_IMAGE,
        contract_sha256=canonical_contract_hash(contract),
        backup_manifest_sha256=hashlib.sha256(
            (root / "manifest.json").read_bytes()
        ).hexdigest(),
        publisher_key_id="migration-publisher",
        signature_ed25519_base64=base64.b64encode(b"0" * 64).decode(),
    )
    signed = unsigned.model_copy(update={
        "signature_ed25519_base64": base64.b64encode(
            signing_key.sign(unsigned.signing_bytes())
        ).decode(),
    })
    (contracts / "demo-addon.attestation.json").write_text(
        json.dumps(signed.model_dump())
    )
    accepted = await engine.upgrade_assessment("demo-addon")
    assert accepted["migration_signature"] == "ed25519_verified"
    assert accepted["can_execute"] is False

    migration["source_image"] = NEXT_IMAGE
    migration_path.write_text(json.dumps(migration))
    rejected = await engine.upgrade_assessment("demo-addon")
    assert rejected["migration_contract"] == "not_verified"
    assert rejected["can_execute"] is False


def test_running_release_cannot_be_reinstalled_as_disguised_upgrade(tmp_path):
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [{
            "addon_id": "demo-addon",
            "version": "2.0.0",
            "image": NEXT_IMAGE,
        }],
    }))
    controller = Controller(approved, tmp_path / "jobs.db", adapter=FakeDocker())
    controller.jobs.set_desired("demo-addon", "1.0.0", IMAGE, "running")
    plan = controller.plan("demo-addon", "install")
    with pytest.raises(JobConflict, match="not an upgrade"):
        controller.jobs.create(
            "replacement-bypass-123", "demo-addon", "install", plan
        )
    assert controller.jobs.desired()[0]["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_stateful_backup_integrity_is_reported_but_never_enables_upgrade(
    tmp_path, monkeypatch
):
    backups = tmp_path / "offline-backups"
    root = backups / "demo-addon"
    root.mkdir(parents=True, mode=0o700)
    data = b"quiesced-sample-data-for-restore"
    (root / "database.dump").write_bytes(data)
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "addon_id": "demo-addon",
        "version": "1.0.0",
        "image": IMAGE,
        "created_at": datetime.now(UTC).isoformat(),
        "source_quiesced": True,
        "backup_kind": "database_export",
        "files": [{
            "name": "database.dump",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }],
    }))
    monkeypatch.setenv("YONDER_CONTROLLER_STATEFUL_BACKUP_ROOT", str(backups))
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [{
            "addon_id": "demo-addon",
            "version": "2.0.0",
            "image": NEXT_IMAGE,
            "data_volume": True,
        }],
    }))
    engine = Controller(approved, tmp_path / "jobs.db", adapter=FakeDocker())
    engine.jobs.set_desired("demo-addon", "1.0.0", IMAGE, "running")
    assessed = await engine.upgrade_assessment("demo-addon")
    assert assessed["backup_integrity"] == "verified_contents_only"
    assert assessed["can_execute"] is False
    assert next(
        item for item in assessed["checks"] if item["name"] == "offline_backup"
    )["status"] == "ready"
    (root / "database.dump").write_bytes(b"tampered")
    second = await engine.upgrade_assessment("demo-addon")
    assert second["backup_integrity"] == "not_verified"
    assert next(
        item for item in second["checks"] if item["name"] == "offline_backup"
    )["status"] == "blocked"
    assert second["can_execute"] is False
