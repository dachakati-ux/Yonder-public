"""Restricted Docker CLI driver. No user-supplied Docker flags or shell."""
from __future__ import annotations

import asyncio
import json
import re

from .models import ApprovedDeployment

CONTAINER_PREFIX = "yonder-addon-"
DOCKER_LABEL = "io.yonder.addon"
OWNER_LABEL = "io.yonder.managed"
HEX_ID = re.compile(r"^[a-f0-9]{12,64}$")


class DockerError(RuntimeError):
    pass


class DockerAdapter:
    def __init__(self, docker_bin: str = "/usr/bin/docker"):
        self.docker_bin = docker_bin

    async def _run(self, *args: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                self.docker_bin, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            )
        except OSError as exc:
            raise DockerError("Docker executable is unavailable") from exc
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise DockerError("Docker operation exceeded its timeout") from exc
        if process.returncode:
            # The Docker output may include sensitive daemon details. Never return it.
            raise DockerError(f"Docker operation failed (exit {process.returncode})")
        return out.decode("utf-8", errors="replace").strip()[:16_384]

    async def _inspect(self, name: str) -> dict | None:
        try:
            result = await self._run("container", "inspect", name)
        except DockerError:
            return None
        try:
            data = json.loads(result)
        except ValueError as exc:
            raise DockerError("Invalid Docker inspection response") from exc
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise DockerError("Malformed container details")
        return data[0]

    @staticmethod
    def _owned(data: dict, addon_id: str) -> bool:
        labels = data.get("Config", {}).get("Labels") or {}
        return (
            labels.get(OWNER_LABEL) == "true"
            and labels.get(DOCKER_LABEL) == addon_id
        )

    async def preflight(self, deployment: ApprovedDeployment) -> None:
        # No arbitrary image names or unpinned image tags from requests.
        result = await self._run("image", "inspect", deployment.image)
        try:
            data = json.loads(result)
            records = data[0]["RepoDigests"]
        except (ValueError, IndexError, TypeError, KeyError) as exc:
            raise DockerError("Could not validate the local pinned image") from exc
        if deployment.image not in records:
            raise DockerError("Local image digest does not match the approved artifact")

    async def install(self, deployment: ApprovedDeployment) -> None:
        name = CONTAINER_PREFIX + deployment.addon_id
        existing = await self._inspect(name)
        if existing is not None:
            if not self._owned(existing, deployment.addon_id):
                raise DockerError("Container name is owned by another application")
            raise DockerError("Add-on is already deployed; explicit upgrade is required")
        await self.preflight(deployment)
        output = await self._run(
            "run", "--detach", "--name", name,
            "--label", f"{OWNER_LABEL}=true",
            "--label", f"{DOCKER_LABEL}={deployment.addon_id}",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(deployment.pids_limit),
            "--memory", f"{deployment.memory_mb}m",
            "--cpus", str(deployment.cpus),
            "--network", deployment.network,
            "--restart", "no",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            deployment.image,
        )
        if not HEX_ID.fullmatch(output):
            raise DockerError("Docker did not return a valid container ID")
        actual = await self._inspect(name)
        if actual is None or not self._owned(actual, deployment.addon_id):
            raise DockerError("Container ownership verification failed")
        if not actual.get("State", {}).get("Running"):
            # Cleanup of the freshly created, verified-owned container.
            await self._run("rm", "--force", name)
            raise DockerError("The new add-on container did not stay running")

    async def remove(self, deployment: ApprovedDeployment) -> None:
        name = CONTAINER_PREFIX + deployment.addon_id
        existing = await self._inspect(name)
        if existing is None:
            return
        if not self._owned(existing, deployment.addon_id):
            raise DockerError("Refusing to remove an unowned container")
        await self._run("rm", "--force", name)

    async def status(self, addon_id: str) -> str:
        data = await self._inspect(CONTAINER_PREFIX + addon_id)
        if data is None:
            return "not_deployed"
        if not self._owned(data, addon_id):
            return "unmanaged_container_collision"
        return "running" if data.get("State", {}).get("Running") else "stopped"
