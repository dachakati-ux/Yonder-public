"""Staged Docker upgrade policy tests without a live daemon or network."""
from __future__ import annotations

import json

import pytest
from controller.docker_adapter import DockerAdapter, DockerError
from controller.models import ApprovedDeployment

OLD_IMAGE = "ghcr.io/example/demo@sha256:" + "a" * 64
NEW_IMAGE = "ghcr.io/example/demo@sha256:" + "b" * 64
JOB = "d4637ceb-8991-4657-983d-69e8e2738642"
ADDON = "demo-addon"
CANONICAL = "yonder-addon-demo-addon"


def approved(**changes):
    data = {
        "addon_id": ADDON, "version": "2.0.0", "image": NEW_IMAGE,
        "readiness": "docker_healthy",
        "upgrade_data_migration": "none",
    }
    data.update(changes)
    return ApprovedDeployment.model_validate(data)


class RecordingUpgradeDocker(DockerAdapter):
    def __init__(self):
        super().__init__()
        self.commands: list[tuple[str, ...]] = []
        self.containers: dict[str, dict] = {
            CANONICAL: {
                "Config": {
                    "Image": OLD_IMAGE,
                    "Labels": {
                        "io.yonder.managed": "true",
                        "io.yonder.addon": ADDON,
                    },
                },
                "State": {"Running": True},
            }
        }

    async def _inspect(self, name):
        return self.containers.get(name)

    async def _run(self, *args, **kwargs):
        self.commands.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"RepoDigests": [NEW_IMAGE]}])
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            labels = {}
            for index, argument in enumerate(args):
                if argument == "--label":
                    key, value = args[index + 1].split("=", 1)
                    labels[key] = value
            self.containers[name] = {
                "Config": {"Image": args[-1], "Labels": labels},
                "State": {"Running": True, "Health": {"Status": "healthy"}},
            }
            return "c" * 64
        if args[0] == "stop":
            self.containers[args[-1]]["State"]["Running"] = False
            return ""
        if args[0] == "start":
            self.containers[args[-1]]["State"]["Running"] = True
            return ""
        if args[0] == "rename":
            previous, replacement = args[1:]
            if replacement in self.containers:
                raise DockerError("Docker rename collision")
            self.containers[replacement] = self.containers.pop(previous)
            return ""
        if args[0] == "rm":
            self.containers.pop(args[-1])
            return ""
        raise AssertionError("Unexpected Docker command: " + str(args))


@pytest.mark.asyncio
async def test_stage_cutover_and_restore_preserve_old_release():
    docker = RecordingUpgradeDocker()
    stage = await docker.stage_stateless(approved(), JOB)
    assert stage.endswith("-stage-d4637ceb")
    assert "--read-only" in next(c for c in docker.commands if c[0] == "run")
    assert "--network" in next(c for c in docker.commands if c[0] == "run")
    assert await docker.readiness(approved(), container_name=stage) == "ready"
    rollback = await docker.cutover_stateless(approved(), OLD_IMAGE, JOB)
    assert rollback.endswith("-rollback-d4637ceb")
    assert docker.containers[rollback]["Config"]["Image"] == OLD_IMAGE
    assert docker.containers[rollback]["State"]["Running"] is False
    assert docker.containers[CANONICAL]["Config"]["Image"] == NEW_IMAGE
    await docker.restore_stateless(approved(), OLD_IMAGE, JOB)
    assert docker.containers[CANONICAL]["Config"]["Image"] == OLD_IMAGE
    assert docker.containers[CANONICAL]["State"]["Running"] is True
    assert docker.containers[stage]["Config"]["Image"] == NEW_IMAGE
    assert not any(cmd[0] == "rm" for cmd in docker.commands)


@pytest.mark.asyncio
async def test_cutover_checks_original_image_before_stopping():
    docker = RecordingUpgradeDocker()
    await docker.stage_stateless(approved(), JOB)
    with pytest.raises(DockerError, match="Existing release identity changed"):
        await docker.cutover_stateless(approved(), NEW_IMAGE, JOB)
    assert not any(cmd[0] in ("stop", "rename") for cmd in docker.commands)
    assert docker.containers[CANONICAL]["State"]["Running"] is True


@pytest.mark.asyncio
async def test_stage_discard_cannot_remove_previous_service():
    docker = RecordingUpgradeDocker()
    stage = await docker.stage_stateless(approved(), JOB)
    await docker.discard_stateless_stage(approved(), JOB)
    assert stage not in docker.containers
    assert CANONICAL in docker.containers
    assert docker.containers[CANONICAL]["State"]["Running"] is True


@pytest.mark.asyncio
async def test_stage_discard_rejects_modified_identity():
    docker = RecordingUpgradeDocker()
    stage = await docker.stage_stateless(approved(), JOB)
    docker.containers[stage]["Config"]["Labels"]["io.yonder.stage"] = "other-job"
    with pytest.raises(DockerError, match="Unverified staged"):
        await docker.discard_stateless_stage(approved(), JOB)
    assert stage in docker.containers


@pytest.mark.asyncio
async def test_stage_rejects_volumes_and_networked_services():
    docker = RecordingUpgradeDocker()
    for changes in (
        {"data_volume": True},
        {"secret_volume": True},
        {"network": "yonder-addon-net-demo-addon"},
    ):
        with pytest.raises(DockerError, match="Only isolated stateless"):
            await docker.stage_stateless(approved(**changes), JOB)
    assert not any(cmd[0] == "run" for cmd in docker.commands)


def test_stage_rejects_untrusted_job_identifier():
    with pytest.raises(DockerError, match="Invalid upgrade job"):
        RecordingUpgradeDocker()._upgrade_names(ADDON, "../outside")
