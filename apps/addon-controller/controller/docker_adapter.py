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
REGISTRY_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::[0-9]{1,5})?$")


class DockerError(RuntimeError):
    pass


def validate_docker_host(raw: str) -> str:
    """Validate an isolated Docker socket-proxy or TLS endpoint.

    Permits local UNIX sockets, loopback TCP socket proxies, or HTTPS endpoints.
    Refuses unencrypted non-loopback TCP and userinfo smuggling.
    """
    value = raw.strip()
    if not value or any(ch.isspace() for ch in value) or "@" in value:
        raise DockerError("Invalid DOCKER_HOST configuration")
    if value.startswith("unix:///"):
        socket_path = value.removeprefix("unix://")
        if ".." in socket_path.split("/"):
            raise DockerError("Invalid UNIX socket path in DOCKER_HOST")
        return value
    if value.startswith(("tcp://127.0.0.1:", "tcp://localhost:")):
        port_part = value.rsplit(":", 1)[-1]
        if not port_part.isdigit() or not (1 <= int(port_part) <= 65535):
            raise DockerError("Invalid loopback TCP port in DOCKER_HOST")
        return value
    if value.startswith("https://"):
        host_port = value.removeprefix("https://")
        if "/" in host_port or not REGISTRY_HOST.fullmatch(host_port.lower()):
            raise DockerError("Invalid HTTPS DOCKER_HOST endpoint")
        return value
    raise DockerError("DOCKER_HOST must be unix:///, loopback tcp://, or https://")


def validate_registry_allowlist(raw: str) -> set[str]:
    """Parse and validate allowed OCI registries; reject wildcards and schemes."""
    entries = {item.strip().lower() for item in raw.split(",") if item.strip()}
    if not entries:
        raise DockerError("OCI registry allowlist cannot be empty")
    for entry in entries:
        if "*" in entry or "@" in entry or "://" in entry or "/" in entry:
            raise DockerError("Invalid OCI registry entry in allowlist")
        if not REGISTRY_HOST.fullmatch(entry):
            raise DockerError("Malformed OCI registry hostname in allowlist")
    return entries


class DockerAdapter:
    def __init__(
        self,
        docker_bin: str = "/usr/bin/docker",
        *,
        docker_host: str | None = None,
    ):
        self.docker_bin = docker_bin
        raw_host = (
            docker_host
            if docker_host is not None
            else os.getenv("YONDER_CONTROLLER_DOCKER_HOST", "")
        )
        self.docker_host = validate_docker_host(raw_host) if raw_host.strip() else None

    async def _run(self, *args: str, timeout: float = 60.0) -> str:
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
        if self.docker_host:
            env["DOCKER_HOST"] = self.docker_host
            if self.docker_host.startswith("https://"):
                env["DOCKER_TLS_VERIFY"] = "1"
                cert_path = os.getenv("YONDER_CONTROLLER_DOCKER_CERT_PATH", "").strip()
                if cert_path:
                    env["DOCKER_CERT_PATH"] = cert_path
        try:
            process = await asyncio.create_subprocess_exec(
                self.docker_bin, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
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
        registry = deployment.image.split("/", 1)[0].lower()
        permitted = validate_registry_allowlist(
            os.getenv("YONDER_CONTROLLER_ALLOWED_REGISTRIES", "ghcr.io")
        )
        if registry not in permitted:
            raise DockerError("OCI registry is not in the operator allowlist")
        # The approved image is a full immutable repository@sha256 digest.
        # Pulling by digest does not itself prove publisher identity: the
        # separate Ed25519 signature gate is mandatory.
        await self._run("pull", "--quiet", deployment.image, timeout=900.0)
        await self.preflight(deployment)

    async def prepare_resources(self, deployment: ApprovedDeployment) -> None:
        """Provision only approved internal networks and persistent data volumes.

        Secrets volumes are NEVER created by this method. Created resources
        survive failed deployments or removal, preserving user data.
        """
        if not deployment.provision_managed_resources:
            return
        if deployment.network != "none":
            names = await self._run(
                "network", "ls", "--filter", f"name=^{deployment.network}$",
                "--format", "{{.Name}}",
            )
            if deployment.network not in names.splitlines():
                await self._run(
                    "network", "create", "--driver", "bridge", "--internal",
                    "--label", "io.yonder.approved=true",
                    "--label", f"{DOCKER_LABEL}={deployment.addon_id}",
                    deployment.network,
                )
        if deployment.data_volume:
            name = f"yonder-addon-data-{deployment.addon_id}"
            names = await self._run(
                "volume", "ls", "--filter", f"name=^{name}$",
                "--format", "{{.Name}}",
            )
            if name not in names.splitlines():
                await self._run(
                    "volume", "create",
                    "--label", "io.yonder.approved=true",
                    "--label", f"{DOCKER_LABEL}={deployment.addon_id}",
                    "--label", "io.yonder.purpose=data",
                    name,
                )
        # Even if names existed already, validate their actual policy labels.
        await self.preflight_resources(deployment)

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

    @staticmethod
    def _upgrade_names(addon_id: str, job_id: str) -> tuple[str, str, str]:
        if not re.fullmatch(r"[a-f0-9-]{36}", job_id):
            raise DockerError("Invalid upgrade job identity")
        base = CONTAINER_PREFIX + addon_id
        suffix = job_id[:8]
        return base, base + "-stage-" + suffix, base + "-rollback-" + suffix

    async def stage_stateless(self, deployment: ApprovedDeployment, job_id: str) -> str:
        """Create a restricted, isolated candidate; leave the old service untouched."""
        if (
            deployment.data_volume or deployment.secret_volume
            or deployment.network != "none"
        ):
            raise DockerError("Only isolated stateless upgrades may be staged")
        _, candidate, _ = self._upgrade_names(deployment.addon_id, job_id)
        if await self._inspect(candidate) is not None:
            raise DockerError("Staging name already exists; manual review required")
        await self.preflight(deployment)
        args: list[str] = []
        for key, value in sorted(deployment.public_settings.items()):
            args.extend(("--env", f"{key}={value}"))
        output = await self._run(
            "run", "--detach", "--name", candidate,
            "--label", f"{OWNER_LABEL}=true",
            "--label", f"{DOCKER_LABEL}={deployment.addon_id}",
            "--label", f"io.yonder.stage={job_id}",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(deployment.pids_limit),
            "--memory", f"{deployment.memory_mb}m",
            "--cpus", str(deployment.cpus),
            "--network", "none", "--restart", "no",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            *args, deployment.image,
        )
        if not HEX_ID.fullmatch(output):
            raise DockerError("Staged container identity not verified")
        found = await self._inspect(candidate)
        if (
            found is None or not self._owned(found, deployment.addon_id)
            or (found.get("Config", {}).get("Labels") or {}).get(
                "io.yonder.stage"
            ) != job_id
            or found.get("Config", {}).get("Image") != deployment.image
            or not found.get("State", {}).get("Running")
        ):
            raise DockerError("Staged container provenance/health could not be verified")
        return candidate

    async def upgrade_recovery_clean(
        self, deployment: ApprovedDeployment, old_image: str, job_id: str
    ) -> bool:
        """True only if the previous release is running under its original name.

        All job-specific staging and rollback names must be absent. Never
        auto-delete or rename containers during this read-only verification.
        """
        canonical, staged, rollback = self._upgrade_names(
            deployment.addon_id, job_id
        )
        candidate = await self._inspect(staged)
        retained = await self._inspect(rollback)
        original = await self._inspect(canonical)
        if candidate is not None or retained is not None or original is None:
            return False
        return bool(
            self._owned(original, deployment.addon_id)
            and original.get("Config", {}).get("Image") == old_image
            and original.get("State", {}).get("Running")
        )

    async def discard_stateless_stage(
        self, deployment: ApprovedDeployment, job_id: str
    ) -> None:
        """Remove only a verified stage container, never the existing service."""
        _, candidate, _ = self._upgrade_names(deployment.addon_id, job_id)
        staged = await self._inspect(candidate)
        if staged is None:
            return
        if (
            not self._owned(staged, deployment.addon_id)
            or (staged.get("Config", {}).get("Labels") or {}).get(
                "io.yonder.stage"
            ) != job_id
            or staged.get("Config", {}).get("Image") != deployment.image
        ):
            raise DockerError("Unverified staged container cannot be removed")
        await self._run("rm", "--force", candidate)

    async def cutover_stateless(
        self, deployment: ApprovedDeployment, old_image: str, job_id: str
    ) -> str:
        """Rename only verified owned containers; preserve old release stopped."""
        canonical, candidate, rollback = self._upgrade_names(
            deployment.addon_id, job_id
        )
        existing = await self._inspect(canonical)
        staged = await self._inspect(candidate)
        if (
            existing is None or not self._owned(existing, deployment.addon_id)
            or existing.get("Config", {}).get("Image") != old_image
            or not existing.get("State", {}).get("Running")
        ):
            raise DockerError("Existing release identity changed; cutover denied")
        if (
            staged is None or not self._owned(staged, deployment.addon_id)
            or staged.get("Config", {}).get("Image") != deployment.image
            or (staged.get("Config", {}).get("Labels") or {}).get(
                "io.yonder.stage"
            ) != job_id
            or not staged.get("State", {}).get("Running")
        ):
            raise DockerError("Staged release identity changed; cutover denied")
        if await self._inspect(rollback) is not None:
            raise DockerError("Rollback name collision; manual review required")
        await self._run("stop", "--time", "10", canonical)
        await self._run("rename", canonical, rollback)
        # An interrupted rename is NOT retried automatically: review job state.
        await self._run("rename", candidate, canonical)
        return rollback

    async def restore_stateless(
        self, deployment: ApprovedDeployment, old_image: str, job_id: str
    ) -> None:
        """Best-effort restoration for a post-cutover health failure.

        Never delete the old release. On errors, the controller must block
        further jobs and require manual review.
        """
        canonical, candidate, rollback = self._upgrade_names(
            deployment.addon_id, job_id
        )
        old = await self._inspect(rollback)
        new = await self._inspect(canonical)
        if (
            old is None or not self._owned(old, deployment.addon_id)
            or old.get("Config", {}).get("Image") != old_image
            or new is None or not self._owned(new, deployment.addon_id)
            or new.get("Config", {}).get("Image") != deployment.image
        ):
            raise DockerError("Cannot verify both releases for safe recovery")
        await self._run("stop", "--time", "10", canonical)
        await self._run("rename", canonical, candidate)
        await self._run("rename", rollback, canonical)
        await self._run("start", canonical)
        returned = await self._inspect(canonical)
        if (
            returned is None or not self._owned(returned, deployment.addon_id)
            or returned.get("Config", {}).get("Image") != old_image
            or not returned.get("State", {}).get("Running")
        ):
            raise DockerError("Original release was not verified after recovery")

    async def remove(self, deployment: ApprovedDeployment) -> None:
        name = CONTAINER_PREFIX + deployment.addon_id
        existing = await self._inspect(name)
        if existing is None:
            return
        if not self._owned(existing, deployment.addon_id):
            raise DockerError("Refusing to remove an unowned container")
        await self._run("rm", "--force", name)

    async def readiness(
        self, deployment: ApprovedDeployment, container_name: str | None = None
    ) -> str:
        """Inspect trusted Docker state; optional image-defined HEALTHCHECK."""
        actual = await self._inspect(
            container_name or CONTAINER_PREFIX + deployment.addon_id
        )
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
