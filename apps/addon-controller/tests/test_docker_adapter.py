"""Restricted Docker adapter command policy tests. No daemon is contacted."""

from __future__ import annotations

import json

import pytest
from controller.docker_adapter import DockerAdapter, DockerError
from controller.models import ApprovedDeployment

IMAGE = "registry.example.org/yonder/demo@sha256:" + "a" * 64
GHCR_IMAGE = "ghcr.io/example/reviewed-addon@sha256:" + "b" * 64


class Recorder(DockerAdapter):
    def __init__(self):
        super().__init__()
        self.commands = []
        self.exists = False
        self.owned = True
        self.running = False

    async def _run(self, *args, **kwargs):
        self.commands.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"RepoDigests": [IMAGE, GHCR_IMAGE]}])
        if args[0] == "pull":
            return ""
        if args[0] == "run":
            self.exists = True
            self.running = True
            return "a" * 64
        if args[0] == "rm":
            self.exists = False
            self.running = False
            return ""
        raise AssertionError("Unexpected Docker command: " + str(args))

    async def _inspect(self, name):
        if not self.exists:
            return None
        labels = {
            "io.yonder.managed": "true" if self.owned else "false",
            "io.yonder.addon": "demo-addon",
        }
        return {"Config": {"Labels": labels}, "State": {"Running": self.running}}


def deployment():
    return ApprovedDeployment(
        addon_id="demo-addon", version="0.1.0",
        image=IMAGE, memory_mb=256, cpus=0.5, pids_limit=64,
    )


@pytest.mark.asyncio
async def test_restricted_install_flags_and_explicit_owner_labels():
    adapter = Recorder()
    await adapter.install(deployment())
    commands = [c for c in adapter.commands if c[0] == "run"]
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd[1:3] == ("--detach", "--name")
    assert "--read-only" in cmd
    assert "--cap-drop" in cmd
    assert "ALL" in cmd
    assert "--security-opt" in cmd
    assert "no-new-privileges" in cmd
    assert "--network" in cmd
    assert "none" in cmd
    assert "--memory" in cmd
    assert "256m" in cmd
    assert "--pids-limit" in cmd
    assert "64" in cmd
    assert "--privileged" not in cmd
    assert "--volume" not in cmd and "-v" not in cmd
    assert "--publish" not in cmd and "-p" not in cmd
    assert cmd[-1] == IMAGE
    await adapter.remove(deployment())
    assert not adapter.exists


@pytest.mark.asyncio
async def test_unowned_container_cannot_be_removed_or_overwritten():
    adapter = Recorder()
    adapter.exists = True
    adapter.owned = False
    with pytest.raises(DockerError, match="unowned"):
        await adapter.remove(deployment())
    with pytest.raises(DockerError, match="another application"):
        await adapter.install(deployment())
    assert not any(c[0] == "rm" for c in adapter.commands)


@pytest.mark.asyncio
async def test_existing_managed_container_cannot_be_overwritten():
    adapter = Recorder()
    adapter.exists = True
    with pytest.raises(DockerError, match="already deployed"):
        await adapter.install(deployment())


@pytest.mark.asyncio
async def test_signed_digest_pull_requires_allowed_registry(monkeypatch):
    adapter = Recorder()
    monkeypatch.setenv("YONDER_CONTROLLER_ALLOWED_REGISTRIES", "ghcr.io")
    approved = ApprovedDeployment(
        addon_id="reviewed-addon",
        version="0.1.0",
        image=GHCR_IMAGE,
        image_acquisition="signed_pull",
    )
    await adapter.acquire(approved)
    assert any(
        command == ("pull", "--quiet", GHCR_IMAGE)
        for command in adapter.commands
    )
    blocked = approved.model_copy(update={"image": IMAGE})
    with pytest.raises(DockerError, match="allowlist"):
        await adapter.acquire(blocked)


@pytest.mark.asyncio
async def test_preloaded_image_cannot_invoke_pull():
    adapter = Recorder()
    with pytest.raises(DockerError, match="not approved"):
        await adapter.acquire(deployment())
