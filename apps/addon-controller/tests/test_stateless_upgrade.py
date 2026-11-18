"""Opt-in staged upgrades: enforce signature, isolation, and rollback boundaries."""

from __future__ import annotations

import json

import pytest
from controller.docker_adapter import DockerError
from controller.engine import Controller, ControllerError

OLD_IMAGE = "ghcr.io/example/demo@sha256:" + "a" * 64
NEW_IMAGE = "ghcr.io/example/demo@sha256:" + "b" * 64


class UpgradeDocker:
    def __init__(self, *, fail_stage=False, fail_cutover=False, fail_health=False):
        self.calls = []
        self.fail_stage = fail_stage
        self.fail_cutover = fail_cutover
        self.fail_health = fail_health
        self.switched = False

    async def preflight(self, deployment):
        self.calls.append(("preflight", deployment.image))

    async def stage_stateless(self, deployment, job_id):
        self.calls.append(("stage", job_id))
        return "yonder-addon-demo-addon-stage-" + job_id[:8]

    async def readiness(self, deployment, container_name=None):
        self.calls.append(("readiness", container_name))
        if container_name and self.fail_stage:
            return "unhealthy"
        if self.switched and self.fail_health:
            return "unhealthy"
        return "ready"

    async def discard_stateless_stage(self, deployment, job_id):
        self.calls.append(("discard", job_id))

    async def cutover_stateless(self, deployment, old_image, job_id):
        self.calls.append(("cutover", old_image))
        if self.fail_cutover:
            raise DockerError("Host rename failed")
        self.switched = True

    async def restore_stateless(self, deployment, old_image, job_id):
        self.calls.append(("restore", old_image))
        self.switched = False

    async def status(self, addon_id):
        return "running"


def controller_fixture(tmp_path, monkeypatch, adapter, **changes):
    deployment = {
        "addon_id": "demo-addon", "version": "2.0.0",
        "image": NEW_IMAGE,
        "upgrade_data_migration": "none",
    }
    deployment.update(changes)
    path = tmp_path / "deployments.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "deployments": [deployment],
    }))
    engine = Controller(path, tmp_path / "jobs.db", adapter=adapter)
    engine.jobs.set_desired("demo-addon", "1.0.0", OLD_IMAGE, "running")
    monkeypatch.setattr(
        "controller.engine.verify_release",
        lambda *_: {"publisher_key_id": "trusted", "verification": "ed25519_valid"},
    )
    return engine


@pytest.mark.asyncio
async def test_verified_staged_stateless_upgrade_retains_previous_release(
    tmp_path, monkeypatch
):
    adapter = UpgradeDocker()
    engine = controller_fixture(tmp_path, monkeypatch, adapter)
    engine.enable_stateless_upgrades = True
    engine.require_signed_releases = True
    plan = engine.plan("demo-addon", "upgrade")
    assert plan["previous"]["image"] == OLD_IMAGE
    assert plan["action"] == "upgrade"
    assert plan["upgrade_strategy"] == "staged_stateless_only"
    job, created = await engine.submit(
        "demo-addon", "upgrade", plan["plan_sha256"], "upgrade-test-1234567"
    )
    assert created
    await engine._tasks[job["id"]]
    finished = engine.jobs.get(job["id"])
    assert finished["status"] == "succeeded"
    assert engine.jobs.desired()[0]["image"] == NEW_IMAGE
    assert any(row[0] == "stage" for row in adapter.calls)
    assert any(row[0] == "cutover" for row in adapter.calls)
    assert not any(row[0] == "restore" for row in adapter.calls)


@pytest.mark.asyncio
async def test_failed_stage_leaves_old_release_and_cleans_candidate(tmp_path, monkeypatch):
    adapter = UpgradeDocker(fail_stage=True)
    engine = controller_fixture(tmp_path, monkeypatch, adapter)
    engine.enable_stateless_upgrades = True
    engine.require_signed_releases = True
    plan = engine.plan("demo-addon", "upgrade")
    job, _ = await engine.submit(
        "demo-addon", "upgrade", plan["plan_sha256"], "upgrade-stage-123456"
    )
    await engine._tasks[job["id"]]
    assert engine.jobs.get(job["id"])["status"] == "failed"
    assert engine.jobs.desired()[0]["image"] == OLD_IMAGE
    assert any(row[0] == "discard" for row in adapter.calls)
    assert not any(row[0] == "cutover" for row in adapter.calls)


@pytest.mark.asyncio
async def test_cutover_failure_requires_operator_recovery(tmp_path, monkeypatch):
    adapter = UpgradeDocker(fail_cutover=True)
    engine = controller_fixture(tmp_path, monkeypatch, adapter)
    engine.enable_stateless_upgrades = True
    engine.require_signed_releases = True
    plan = engine.plan("demo-addon", "upgrade")
    job, _ = await engine.submit(
        "demo-addon", "upgrade", plan["plan_sha256"], "upgrade-cutover-1234"
    )
    await engine._tasks[job["id"]]
    assert engine.jobs.get(job["id"])["status"] == "interrupted"
    assert engine.jobs.desired()[0]["image"] == OLD_IMAGE
    with pytest.raises(ControllerError, match="staging/rollback containers"):
        await engine.acknowledge(job["id"], "running")
    with pytest.raises(ControllerError, match="already active"):
        await engine.submit(
            "demo-addon", "upgrade", plan["plan_sha256"], "new-upgrade-123456"
        )
    adapter.recovery_clean = True
    recovered = await engine.acknowledge(job["id"], "running")
    assert recovered["status"] == "acknowledged"



@pytest.mark.asyncio
async def test_post_cutover_failure_restores_previous_stateless_release(
    tmp_path, monkeypatch
):
    adapter = UpgradeDocker(fail_health=True)
    engine = controller_fixture(tmp_path, monkeypatch, adapter)
    engine.enable_stateless_upgrades = True
    engine.require_signed_releases = True
    plan = engine.plan("demo-addon", "upgrade")
    job, _ = await engine.submit(
        "demo-addon", "upgrade", plan["plan_sha256"], "upgrade-restore-1234"
    )
    await engine._tasks[job["id"]]
    assert engine.jobs.get(job["id"])["status"] == "failed"
    assert engine.jobs.desired()[0]["image"] == OLD_IMAGE
    assert any(row[0] == "restore" for row in adapter.calls)


def test_stateless_upgrade_requires_operator_opt_in_signature_and_no_data(
    tmp_path, monkeypatch
):
    engine = controller_fixture(tmp_path, monkeypatch, UpgradeDocker())
    with pytest.raises(ControllerError, match="disabled"):
        engine.plan("demo-addon", "upgrade")
    engine.enable_stateless_upgrades = True
    with pytest.raises(ControllerError, match="signed"):
        engine.plan("demo-addon", "upgrade")
    engine.require_signed_releases = True
    assert engine.plan("demo-addon", "upgrade")["action"] == "upgrade"
    old_policy = engine.approved()["demo-addon"]
    engine.approved = lambda: {
        "demo-addon": old_policy.model_copy(update={"data_volume": True})
    }
    with pytest.raises(ControllerError, match="stateless"):
        engine.plan("demo-addon", "upgrade")
