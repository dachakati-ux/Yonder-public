"""Standalone controller tests, without a Docker daemon or network."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from controller.api import create_app
from controller.engine import Controller, ControllerError, load_deployments
from controller.models import ApprovedDeployment
from controller.store import JobStore

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
