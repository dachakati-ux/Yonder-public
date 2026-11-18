"""Restricted Docker CLI driver. No user-supplied Docker flags or shell."""
from __future__ import annotations

import asyncio
import json
import os
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

    async def _run(self, *args: str, timeout: float = 60.0) -> str:
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
            out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise DockerError("Docker operation exceeded its timeout") from exc
        if process.returncode:
            # The Docker output may include sensitive daemon details. Never return it.
            raise DockerError(f"Docker operation failed (exit {process.returncode})")
        return out.decode("utf-8", errors="replace").strip()[:16_384]

    async def _inspect(self, name: str) -> dict | None:
        # Distinguish an absent container from an unavailable Docker daemon.
        # Never silently treat a connection/permission error as "not installed".
        names = await self._run(
            "container", "ls", "--all", "--filter", f"name=^/{name}$",
            "--format", "{{.Names}}",
        )
        if name not in names.splitlines():
            return None
        result = await self._run("container", "inspect", name)
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

    async def acquire(self, deployment: ApprovedDeployment) -> None:
        """Fetch an exact publisher-signed OCI digest from an approved registry.

        The controller verifies publisher signatures before this method is invoked.
        No arbitrary tags, registry logins, or user-specified Docker flags.
        """
        if deployment.image_acquisition != "signed_pull":
            raise DockerError("Remote acquisition is not approved for this add-on")
        registry = deployment.image.split("/", 1)[0]
        permitted = {
            name.strip().lower()
            for name in os.getenv(
                "YONDER_CONTROLLER_ALLOWED_REGISTRIES", "ghcr.io"
            ).split(",")
            if name.strip()
        }
        if registry not in permitted:
            raise DockerError("OCI registry is not in the operator allowlist")
        # The approved image is a full immutable repository@sha256 digest.
        # Pulling by digest does not itself prove publisher identity: the
        # separate Ed25519 signature gate is mandatory.
        await self._run("pull", "--quiet", deployment.image, timeout=900.0)
        await self.preflight(deployment)

    async def preflight_resources(self, deployment: ApprovedDeployment) -> None:
        """Check that every referenced resource already exists and is approved."""
        if deployment.network != "none":
            response = await self._run("network", "inspect", deployment.network)
            try:
                network = json.loads(response)[0]
                labels = network.get("Labels") or {}
                if (
                    labels.get("io.yonder.approved") != "true"
                    or labels.get(DOCKER_LABEL) != deployment.addon_id
                    or network.get("Internal") is not True
                    or network.get("Driver") != "bridge"
                ):
                    raise DockerError("Add-on network policy is not approved")
            except (ValueError, IndexError, TypeError, KeyError) as exc:
                raise DockerError("Invalid network policy response") from exc
        for kind, enabled in (
            ("secrets", deployment.secret_volume),
            ("data", deployment.data_volume),
        ):
            if not enabled:
                continue
            name = f"yonder-addon-{kind}-{deployment.addon_id}"
            result = await self._run("volume", "inspect", name)
            try:
                volume = json.loads(result)[0]
                labels = volume.get("Labels") or {}
                if (
                    volume.get("Name") != name
                    or labels.get("io.yonder.approved") != "true"
                    or labels.get(DOCKER_LABEL) != deployment.addon_id
                    or labels.get("io.yonder.purpose") != kind
                ):
                    raise DockerError(f"Add-on {kind} volume policy is not approved")
            except (ValueError, IndexError, TypeError, KeyError) as exc:
                raise DockerError("Invalid volume policy response") from exc

    async def install(self, deployment: ApprovedDeployment) -> None:
        name = CONTAINER_PREFIX + deployment.addon_id
        existing = await self._inspect(name)
        if existing is not None:
            if not self._owned(existing, deployment.addon_id):
                raise DockerError("Container name is owned by another application")
            raise DockerError("Add-on is already deployed; explicit upgrade is required")
        await self.preflight(deployment)
        await self.preflight_resources(deployment)
        extra: list[str] = []
        for key, value in sorted(deployment.public_settings.items()):
            extra.extend(("--env", f"{key}={value}"))
        if deployment.secret_volume:
            extra.extend((
                "--mount", "type=volume,source=yonder-addon-secrets-"
                + deployment.addon_id + ",target=/run/yonder-secrets,readonly",
            ))
        if deployment.data_volume:
            extra.extend((
                "--mount", "type=volume,source=yonder-addon-data-"
                + deployment.addon_id + ",target=/var/lib/yonder-addon",
            ))
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
            *extra,
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

    async def readiness(self, deployment: ApprovedDeployment) -> str:
        """Inspect trusted Docker state; optional image-defined HEALTHCHECK."""
        actual = await self._inspect(CONTAINER_PREFIX + deployment.addon_id)
        if actual is None:
            return "not_deployed"
        if not self._owned(actual, deployment.addon_id):
            return "unmanaged_container_collision"
        state = actual.get("State", {})
        if not state.get("Running"):
            return "stopped"
        if deployment.readiness == "running":
            return "ready"
        health = state.get("Health")
        if not isinstance(health, dict):
            return "missing_healthcheck"
        result = health.get("Status", "")
        if result == "healthy":
            return "ready"
        if result == "unhealthy":
            return "unhealthy"
        return "starting"

    async def status(self, addon_id: str) -> str:
        data = await self._inspect(CONTAINER_PREFIX + addon_id)
        if data is None:
            return "not_deployed"
        if not self._owned(data, addon_id):
            return "unmanaged_container_collision"
        return "running" if data.get("State", {}).get("Running") else "stopped"
