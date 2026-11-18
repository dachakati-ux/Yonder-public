"""Read-only preflight reports cannot provision anything or bypass setup."""
from __future__ import annotations

import json

import pytest

from controller.docker_adapter import DockerError
from controller.engine import Controller


class InspectOnlyAdapter:
    def __init__(self, *, missing_secrets=False):
        self.calls = []
        self.missing_secrets = missing_secrets

    async def _run(self, *args, **kwargs):
        self.calls.append(args)
        if args[:1] == ("version",):
            return "28.0.0"
        raise AssertionError("Preflight invoked an unexpected Docker action")

    async def preflight(self, deployment):
        self.calls.append(("preflight", deployment.addon_id))

    async def preflight_resources(self, deployment):
        self.calls.append(("inspect_resources", deployment.addon_id))
        if self.missing_secrets and deployment.secret_volume:
            raise DockerError("Missing approved secrets volume")


def approved_config(tmp_path, *, setup=False, secrets=False):
    config = {
        "schema_version": 1,
        "deployments": [{
            "addon_id": "classroom",
            "version": "0.1.0",
            "image": "ghcr.io/example/classroom@sha256:" + "a" * 64,
            "secret_volume": secrets,
            "setup_fields": (
                [{"key": "LESSON_MODE", "label": "Lesson mode", "kind": "boolean"}]
                if setup else []
            ),
        }],
    }
    path = tmp_path / "approved.json"
    path.write_text(json.dumps(config))
    return path


@pytest.mark.asyncio
async def test_read_only_preflight_is_safe_and_reports_ready(tmp_path):
    adapter = InspectOnlyAdapter()
    engine = Controller(
        approved_config(tmp_path), tmp_path / "jobs.db", adapter=adapter
    )
    result = await engine.preflight_report("classroom")
    assert result["can_submit"] is True
    assert all(item["status"] == "ready" for item in result["checks"])
    assert adapter.calls
    assert not any(
        item[0] in ("run", "pull", "rm", "create") for item in adapter.calls
    )


@pytest.mark.asyncio
async def test_required_setup_and_secrets_block_preflight(tmp_path):
    adapter = InspectOnlyAdapter(missing_secrets=True)
    engine = Controller(
        approved_config(tmp_path, setup=True, secrets=True),
        tmp_path / "jobs.db",
        adapter=adapter,
    )
    result = await engine.preflight_report("classroom")
    assert result["can_submit"] is False
    blocked = {item["name"] for item in result["checks"] if item["status"] == "blocked"}
    assert {"configuration", "release", "secrets"}.issubset(blocked)
    assert not any(item[0] in ("run", "pull") for item in adapter.calls)


@pytest.mark.asyncio
async def test_complete_setup_only_clears_its_own_blocker(tmp_path):
    adapter = InspectOnlyAdapter(missing_secrets=True)
    engine = Controller(
        approved_config(tmp_path, setup=True, secrets=True),
        tmp_path / "jobs.db", adapter=adapter,
    )
    await engine.configure("classroom", {"LESSON_MODE": "true"})
    result = await engine.preflight_report("classroom")
    checks = {item["name"]: item["status"] for item in result["checks"]}
    assert checks["configuration"] == "ready"
    assert checks["release"] == "ready"
    assert checks["secrets"] == "blocked"
    assert result["can_submit"] is False
