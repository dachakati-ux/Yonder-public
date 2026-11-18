"""Read-only upgrade assessments must not execute Docker or mutate data."""
from __future__ import annotations

import json

import pytest
from controller.engine import Controller
from controller.models import ApprovedDeployment
from controller.upgrade_planner import UpgradePolicy, assess_upgrade
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
