"""M02.11 fault-injection tests: Docker outage, disk full (ENOSPC), failed migration & restart."""

from __future__ import annotations

import errno
import json
import sqlite3
from pathlib import Path

import pytest
from controller.credential_vault import CredentialVault, VaultError, create_master_key
from controller.docker_adapter import DockerError
from controller.engine import Controller
from controller.sqlite_backup import BackupError, snapshot_sqlite_bundle
from controller.store import JobStore

IMAGE = "ghcr.io/example/demo@sha256:" + "a" * 64


class DownDockerAdapter:
    async def _run(self, *_args, **_kwargs) -> str:
        raise DockerError("Cannot connect to the Docker daemon")

    async def preflight(self, _deployment) -> None:
        raise DockerError("Cannot connect to the Docker daemon")

    async def preflight_resources(self, _deployment) -> None:
        raise DockerError("Cannot connect to the Docker daemon")

    async def inspect_status(self, _addon_id: str) -> str:
        raise DockerError("Cannot connect to the Docker daemon")

    async def install(self, _deployment) -> None:
        raise DockerError("Cannot connect to the Docker daemon")

    async def remove(self, _deployment) -> None:
        raise DockerError("Cannot connect to the Docker daemon")


def _deployments_file(tmp_path: Path) -> Path:
    path = tmp_path / "deployments.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [{
            "addon_id": "demo-addon",
            "version": "1.0.0",
            "image": IMAGE,
        }],
    }))
    return path


@pytest.mark.asyncio
async def test_fault_docker_daemon_down_fails_cleanly_without_corrupting_state(tmp_path) -> None:
    engine = Controller(
        _deployments_file(tmp_path),
        tmp_path / "jobs.db",
        adapter=DownDockerAdapter(),
    )
    preflight = await engine.preflight_report("demo-addon")
    assert preflight["can_submit"] is False

    plan = engine.plan("demo-addon", "install")
    job, created = await engine.submit(
        "demo-addon", "install", plan["plan_sha256"], "fault-op-0001"
    )
    assert created is True
    await engine._tasks[job["id"]]
    stored = engine.jobs.get(job["id"])
    assert stored["status"] == "failed"
    assert engine.jobs.desired() == []


def test_fault_enospc_during_vault_and_sqlite_backup_leaves_no_corrupt_file(
    tmp_path, monkeypatch
) -> None:
    key = tmp_path / "master.key"
    create_master_key(key)
    vault = CredentialVault(tmp_path / "vault.db", key)
    vault.put("demo-addon", "API_TOKEN", b"top-secret-value")

    def raise_enospc(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("controller.credential_vault.os.fsync", raise_enospc)
    vault_backup = tmp_path / "vault-backup.db"
    with pytest.raises(VaultError, match="backup failed"):
        vault.backup(vault_backup)
    assert not vault_backup.exists()
    # Original vault remains readable.
    assert vault.read_for_offline_delivery("demo-addon", "API_TOKEN") == b"top-secret-value"

    monkeypatch.setattr("controller.sqlite_backup.os.fsync", raise_enospc)
    live_db = tmp_path / "live.db"
    with sqlite3.connect(live_db) as conn:
        conn.execute("CREATE TABLE t (x INT)")
        conn.execute("INSERT INTO t VALUES (1)")
    dest_dir = tmp_path / "db-backup"
    with pytest.raises((BackupError, OSError)):
        snapshot_sqlite_bundle(
            live_db,
            dest_dir,
            addon_id="demo-addon",
            version="1.0.0",
            image=IMAGE,
        )
    assert not (dest_dir / "manifest.json").exists()


def test_fault_restart_interrupts_inflight_jobs_without_auto_resume(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    plan = {"addon_id": "demo-addon", "action": "install", "plan_sha256": "a" * 64}
    job, created = store.create("restart-op-0001", "demo-addon", "install", plan)
    assert created is True
    assert job["status"] == "queued"

    # Simulate controller process crash & restart.
    reopened = JobStore(db_path)
    interrupted = reopened.get(job["id"])
    assert interrupted is not None
    assert interrupted["status"] == "interrupted"
