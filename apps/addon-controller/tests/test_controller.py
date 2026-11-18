"""Standalone controller tests, without a Docker daemon or network."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from controller.api import create_app
from controller.docker_adapter import DockerError
from controller.engine import Controller, ControllerError, load_deployments
from controller.models import ApprovedDeployment
from controller.store import JobStore
from fastapi.testclient import TestClient
from pydantic import ValidationError

IMAGE = "registry.example.org/yonder/demo@sha256:" + "a" * 64
TOKEN = "A" * 40


class FakeDocker:
    def __init__(self):
        self.running: set[str] = set()
        self.actions: list[str] = []

    async def preflight(self, deployment):
        self.actions.append("preflight")

    async def install(self, deployment):
        self.actions.append("install")
        self.running.add(deployment.addon_id)

    async def remove(self, deployment):
        self.actions.append("remove")
        self.running.discard(deployment.addon_id)

    async def status(self, addon_id):
        return "running" if addon_id in self.running else "not_deployed"

    async def readiness(self, deployment):
        return "ready" if deployment.addon_id in self.running else "not_deployed"


def config(tmp_path: Path) -> Path:
    path = tmp_path / "deployments.json"
    data = {
        "schema_version": 1,
        "deployments": [{
            "addon_id": "demo-addon",
            "version": "0.1.0",
            "image": IMAGE,
            "memory_mb": 256,
            "cpus": 0.5,
        }],
    }
    path.write_text(json.dumps(data))
    return path


def test_exact_digest_and_no_untrusted_runtime_options():
    assert ApprovedDeployment(
        addon_id="demo-addon", image=IMAGE, version="0.1.0"
    ).network == "none"
    with pytest.raises(ValidationError):
        ApprovedDeployment(
            addon_id="demo-addon", image="ubuntu:latest", version="0.1.0"
        )
    with pytest.raises(ValidationError):
        ApprovedDeployment(
            addon_id="demo-addon", image=IMAGE,
            version="0.1.0", privileged=True
        )
    with pytest.raises(ValidationError):
        ApprovedDeployment(
            addon_id="demo-addon", image=IMAGE,
            version="0.1.0", memory_mb=99_999
        )


def test_approval_hash_changes_with_deployment_limits():
    one = ApprovedDeployment(
        addon_id="demo-addon", image=IMAGE, version="0.1.0"
    )
    two = one.model_copy(update={"memory_mb": 1024})
    assert one.plan("install")["plan_sha256"] != two.plan("install")["plan_sha256"]


def test_private_allowlist_and_duplicate_detection(tmp_path):
    path = config(tmp_path)
    assert "demo-addon" in load_deployments(path)
    data = json.loads(path.read_text())
    data["deployments"].append(data["deployments"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ControllerError):
        load_deployments(path)
    if os.name == "posix":
        os.chmod(path, 0o666)
        with pytest.raises(ControllerError):
            load_deployments(path)


def test_durable_job_store_idempotency_and_restart(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    plan = ApprovedDeployment(
        addon_id="demo-addon", image=IMAGE, version="0.1.0"
    ).plan("install")
    item, new = store.create("my-request-abc123", "demo-addon", "install", plan)
    assert new and item["status"] == "queued"
    again, new = store.create("my-request-abc123", "demo-addon", "install", plan)
    assert not new and again["id"] == item["id"]
    reopened = JobStore(tmp_path / "jobs.db")
    assert reopened.get(item["id"])["status"] == "interrupted"


@pytest.mark.asyncio
async def test_deploy_and_remove_end_to_end_with_stub(tmp_path):
    adapter = FakeDocker()
    engine = Controller(config(tmp_path), tmp_path / "state.db", adapter=adapter)
    plan = engine.plan("demo-addon", "install")
    with pytest.raises(ControllerError, match="changed"):
        await engine.submit("demo-addon", "install", "0" * 64, "first-operation-123")
    job, created = await engine.submit(
        "demo-addon", "install", plan["plan_sha256"], "first-operation-123"
    )
    assert created
    await engine._tasks[job["id"]]
    assert engine.jobs.get(job["id"])["status"] == "succeeded"
    assert "demo-addon" in adapter.running
    removal = engine.plan("demo-addon", "remove")
    job, created = await engine.submit(
        "demo-addon", "remove", removal["plan_sha256"], "second-operation-123"
    )
    assert created
    await engine._tasks[job["id"]]
    assert engine.jobs.get(job["id"])["status"] == "succeeded"
    assert "demo-addon" not in adapter.running


def test_controller_api_requires_authorization(tmp_path):
    adapter = FakeDocker()
    engine = Controller(config(tmp_path), tmp_path / "state.db", adapter=adapter)
    app = create_app(token=TOKEN, controller=engine)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/approved").status_code == 401
        headers = {"authorization": "Bearer " + TOKEN}
        approved = client.get("/v1/approved", headers=headers)
        assert approved.status_code == 200
        plan = client.get(
            "/v1/plans/demo-addon", headers=headers
        ).json()["plan"]
        response = client.post(
            "/v1/jobs", headers=headers,
            json={
                "addon_id": "demo-addon",
                "action": "install",
                "plan_sha256": plan["plan_sha256"],
                "idempotency_key": "ui-approved-12345",
            },
        )
        assert response.status_code == 202
        assert response.json()["created"] is True
        assert client.get("/v1/jobs", headers=headers).status_code == 200
        assert client.post("/v1/jobs", json={}).status_code == 401


@pytest.mark.asyncio
async def test_interrupted_jobs_require_explicit_observed_state_review(tmp_path):
    allowlist = config(tmp_path)
    adapter = FakeDocker()
    previous = Controller(allowlist, tmp_path / "state.db", adapter=adapter)
    plan = previous.plan("demo-addon", "install")
    job, created = previous.jobs.create(
        "interrupted-op-12345", "demo-addon", "install", plan
    )
    assert created and job["status"] == "queued"

    restarted = Controller(allowlist, tmp_path / "state.db", adapter=adapter)
    assert restarted.jobs.get(job["id"])["status"] == "interrupted"
    with pytest.raises(ControllerError, match="already active"):
        await restarted.submit(
            "demo-addon", "install", plan["plan_sha256"], "fresh-op-12345678"
        )
    with pytest.raises(ControllerError, match="changed"):
        await restarted.acknowledge(job["id"], "running")

    reviewed = await restarted.acknowledge(job["id"], "not_deployed")
    assert reviewed["status"] == "acknowledged"
    new_job, created = await restarted.submit(
        "demo-addon", "install", plan["plan_sha256"], "fresh-op-12345678"
    )
    assert created
    await restarted._tasks[new_job["id"]]
    assert restarted.jobs.get(new_job["id"])["status"] == "succeeded"


@pytest.mark.asyncio
async def test_failed_readiness_removes_only_new_container(tmp_path):
    class UnhealthyDocker(FakeDocker):
        async def readiness(self, deployment):
            return "unhealthy"

    adapter = UnhealthyDocker()
    engine = Controller(config(tmp_path), tmp_path / "failed.db", adapter=adapter)
    plan = engine.plan("demo-addon", "install")
    job, created = await engine.submit(
        "demo-addon", "install", plan["plan_sha256"], "unhealthy-test-123"
    )
    assert created
    await engine._tasks[job["id"]]
    result = engine.jobs.get(job["id"])
    assert result["status"] == "failed"
    assert "new container removed" in result["message"]
    assert adapter.actions == ["preflight", "install", "remove"]


@pytest.mark.asyncio
async def test_failed_rollback_blocks_followup_until_review(tmp_path):
    class StuckDocker(FakeDocker):
        async def readiness(self, deployment):
            return "unhealthy"

        async def remove(self, deployment):
            raise DockerError("Cannot remove")

    adapter = StuckDocker()
    engine = Controller(config(tmp_path), tmp_path / "stuck.db", adapter=adapter)
    plan = engine.plan("demo-addon", "install")
    job, _ = await engine.submit(
        "demo-addon", "install", plan["plan_sha256"], "rollback-test-123"
    )
    await engine._tasks[job["id"]]
    assert engine.jobs.get(job["id"])["status"] == "interrupted"
    with pytest.raises(ControllerError, match="already active"):
        await engine.submit(
            "demo-addon", "install", plan["plan_sha256"], "new-request-1234"
        )


@pytest.mark.parametrize(
    "settings",
    [
        {"API_TOKEN": "should-not-be-here"},
        {"ADMIN_PASSWORD": "never"},
        {"DATABASE_SECRET": "example"},
        {"__PYTHONPATH": "/host"},
        {"ENV": "line\nbreak"},
        {"SAFE_MODE": "x" * 300},
    ],
)
def test_secret_or_unsafe_public_environment_rejected(settings):
    with pytest.raises(ValidationError):
        ApprovedDeployment(
            addon_id="demo-addon", image=IMAGE, version="0.1.0",
            public_settings=settings,
        )


def test_network_must_be_dedicated_and_not_host_or_bridge():
    for network in ("host", "bridge", "yonder-addon-net-another-addon"):
        with pytest.raises(ValidationError):
            ApprovedDeployment(
                addon_id="demo-addon", image=IMAGE, version="0.1.0",
                network=network,
            )


def test_config_and_mounts_included_in_plan_approval_digest():
    base = ApprovedDeployment(
        addon_id="demo-addon", image=IMAGE, version="0.1.0"
    )
    a = base.model_copy(update={"public_settings": {"SAFE_MODE": "yes"}})
    b = base.model_copy(update={"secret_volume": True})
    c = base.model_copy(update={"data_volume": True})
    d = base.model_copy(update={"network": "yonder-addon-net-demo-addon"})
    original = base.plan("install")["plan_sha256"]
    assert all(
        version.plan("install")["plan_sha256"] != original
        for version in (a, b, c, d)
    )
