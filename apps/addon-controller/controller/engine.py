"""Controller orchestration. Only exact operator-approved deployment plans run."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from pydantic import ValidationError

from .docker_adapter import DockerAdapter, DockerError
from .models import ApprovedDeployment, ControllerConfig
from .release_trust import ReleaseTrustError, verify_release
from .store import JobConflict, JobStore

APPROVAL = re.compile(r"^[a-f0-9]{64}$")
KEY = re.compile(r"^[A-Za-z0-9_-]{12,128}$")


class ControllerError(ValueError):
    pass


def load_deployments(path: Path) -> dict[str, ApprovedDeployment]:
    if not path.is_file() or path.is_symlink():
        raise ControllerError("An approved, non-symlink deployment file is required")
    mode = path.stat().st_mode
    if os.name == "posix" and mode & 0o022:
        raise ControllerError("Deployment file must not be writable by group/others")
    if path.stat().st_size > 100_000:
        raise ControllerError("Deployment configuration is too large")
    try:
        parsed = ControllerConfig.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise ControllerError("Invalid approved deployment configuration") from exc
    return {item.addon_id: item for item in parsed.deployments}


class Controller:
    def __init__(
        self, config: Path, database: Path, adapter: DockerAdapter | None = None
    ):
        self.config = config
        self.jobs = JobStore(database)
        self.docker = adapter or DockerAdapter()
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self.require_signed_releases = (
            os.getenv("YONDER_CONTROLLER_REQUIRE_SIGNATURES", "false").lower()
            in ("true", "1", "yes")
        )
        self.release_root = Path(
            os.getenv("YONDER_CONTROLLER_RELEASE_ROOT", "/etc/yonder/releases")
        )
        self.publisher_trust = Path(
            os.getenv("YONDER_CONTROLLER_PUBLISHER_KEYS", "/etc/yonder/publishers.json")
        )

    def approved(self) -> dict[str, ApprovedDeployment]:
        return load_deployments(self.config)

    def setup(self, addon_id: str) -> dict:
        deployment = self.approved().get(addon_id)
        if deployment is None:
            raise ControllerError("Add-on is not approved for setup")
        saved = self.jobs.get_setup(addon_id)
        fields = deployment.setup_fields
        effective = {
            field.key: saved.get(
                field.key, field.default if field.default is not None else ""
            )
            for field in fields
        }
        return {
            "addon_id": addon_id,
            "fields": [field.model_dump() for field in fields],
            "values": effective,
            "configured": all(
                (not field.required or bool(effective[field.key]))
                and (not effective[field.key]
                     or self._safe_field(field, effective[field.key]))
                for field in fields
            ),
            "locked_settings": sorted(deployment.public_settings),
            "secrets_required": deployment.secret_volume,
            "note": "Only non-secret fields are accepted. Secrets are provisioned separately.",
        }

    @staticmethod
    def _safe_field(field, value: str) -> bool:
        try:
            field.validate_value(value)
            return True
        except ValueError:
            return False

    async def configure(self, addon_id: str, values: dict[str, str]) -> dict:
        async with self._lock:
            deployment = self.approved().get(addon_id)
            if deployment is None:
                raise ControllerError("Add-on is not approved for setup")
            fields = {field.key: field for field in deployment.setup_fields}
            if not fields:
                raise ControllerError("This add-on has no editable setup fields")
            if set(values) != set(fields):
                raise ControllerError("Provide only the declared setup fields")
            try:
                for name, value in values.items():
                    fields[name].validate_value(value)
                self.jobs.save_setup(addon_id, values)
            except (ValueError, JobConflict) as exc:
                raise ControllerError("Setup rejected: " + str(exc)) from exc
            return self.setup(addon_id)

    def effective(self, addon_id: str) -> ApprovedDeployment:
        deployment = self.approved().get(addon_id)
        if deployment is None:
            raise ControllerError("Add-on is not approved for deployment on this host")
        state = self.setup(addon_id)
        if not state["configured"]:
            raise ControllerError("Complete the declared setup fields first")
        configured = {
            key: value
            for key, value in state["values"].items()
            if value != ""
        }
        return deployment.model_copy(
            update={"public_settings": {**deployment.public_settings, **configured}}
        )

    def plan(self, addon_id: str, action: str) -> dict:
        if action not in ("install", "remove"):
            raise ControllerError("Unsupported action")
        selected = self.effective(addon_id) if action == "install" else (
            self.approved().get(addon_id)
        )
        if selected is None:
            raise ControllerError("Add-on is not approved for deployment on this host")
        plan = selected.plan(action)
        if (
            action == "install"
            and selected.image_acquisition == "signed_pull"
            and not self.require_signed_releases
        ):
            raise ControllerError("Remote image pulling requires signed release enforcement")
        if action == "install":
            if self.require_signed_releases:
                try:
                    proof = verify_release(
                        selected,
                        self.release_root / f"{addon_id}.json",
                        self.publisher_trust,
                    )
                except ReleaseTrustError as exc:
                    raise ControllerError("Add-on publisher verification failed") from exc
                plan["publisher_key_id"] = proof["publisher_key_id"]
                plan["publisher_verification"] = "ed25519_valid"
            else:
                plan["publisher_verification"] = "not_enforced"
        else:
            plan["publisher_verification"] = "not_applicable"
        plan.pop("plan_sha256")
        data = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
        plan["plan_sha256"] = hashlib.sha256(data).hexdigest()
        return plan

    async def preflight_report(self, addon_id: str) -> dict:
        """Read-only requirement checks; never starts Docker or pulls an image."""
        checks: list[dict[str, str]] = []

        def check(name: str, state: str, message: str) -> None:
            checks.append({"name": name, "status": state, "message": message})

        try:
            current_setup = self.setup(addon_id)
        except ControllerError:
            raise ControllerError("Add-on is not approved on this host") from None
        if current_setup["configured"]:
            check("configuration", "ready", "Required non-secret settings are complete.")
        else:
            check("configuration", "blocked", "Complete the setup wizard first.")

        policy = self.approved()[addon_id]
        try:
            self.plan(addon_id, "install")
        except ControllerError:
            check("release", "blocked", "Approved publisher/release or setup is incomplete.")
        else:
            check("release", "ready", "Approved release and plan can be verified.")

        try:
            await self.docker._run("version", "--format", "{{.Server.Version}}")
        except DockerError:
            check("docker", "blocked", "Controller cannot contact Docker daemon.")
        else:
            check("docker", "ready", "Docker daemon is reachable.")

        if policy.image_acquisition == "signed_pull":
            check("image", "managed", "Signed immutable image is acquired after approval.")
        else:
            try:
                await self.docker.preflight(policy)
            except DockerError:
                check("image", "blocked", "Approved image digest is not available locally.")
            else:
                check("image", "ready", "Approved image digest is already present.")

        if policy.secret_volume:
            secret_only = policy.model_copy(update={
                "network": "none", "data_volume": False,
            })
            try:
                await self.docker.preflight_resources(secret_only)
            except DockerError:
                check(
                    "secrets", "blocked",
                    "Administrator must provision the approved read-only secrets volume.",
                )
            else:
                check("secrets", "ready", "Approved secret-volume labels are verified.")
        else:
            check("secrets", "ready", "No credentials are requested.")

        if policy.provision_managed_resources:
            check(
                "resources", "managed",
                "Controller may create approved data/network resources after approval.",
            )
        else:
            resources_only = policy.model_copy(update={"secret_volume": False})
            try:
                await self.docker.preflight_resources(resources_only)
            except DockerError:
                check(
                    "resources", "blocked",
                    "Required approved data volume or network is missing or untrusted.",
                )
            else:
                check("resources", "ready", "Required resource labels are verified.")

        return {
            "addon_id": addon_id,
            "checks": checks,
            "can_submit": all(row["status"] != "blocked" for row in checks),
            "note": (
                "Read-only preflight is a snapshot. The controller validates all "
                "policies again when executing an approved installation."
            ),
        }

    async def submit(
        self, addon_id: str, action: str, digest: str, key: str
    ) -> tuple[dict, bool]:
        if not APPROVAL.fullmatch(digest) or not KEY.fullmatch(key):
            raise ControllerError("Invalid plan approval or idempotency key")
        async with self._lock:
            plan = self.plan(addon_id, action)
            if digest != plan["plan_sha256"]:
                raise ControllerError(
                    "Approved plan has changed; review and approve the current plan"
                )
            try:
                job, created = self.jobs.create(key, addon_id, action, plan)
            except JobConflict as exc:
                raise ControllerError(str(exc)) from exc
            if created:
                task = asyncio.create_task(self._execute(job["id"]))
                self._tasks[job["id"]] = task
                task.add_done_callback(
                    lambda _task, jid=job["id"]: self._tasks.pop(jid, None)
                )
            return job, created

    async def _execute(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        deployment: ApprovedDeployment | None = None
        try:
            self.jobs.transition(job_id, "preflight")
            action = job["action"]
            deployment = (
                self.effective(job["addon_id"]) if action == "install"
                else self.approved().get(job["addon_id"])
            )
            if deployment is None:
                raise ControllerError("Approved deployment is no longer available")
            if self.plan(deployment.addon_id, action)["plan_sha256"] != job["plan_sha256"]:
                raise ControllerError("Deployment changed before execution")
            if action == "install":
                if deployment.image_acquisition == "signed_pull":
                    self.jobs.transition(job_id, "acquiring")
                    await self.docker.acquire(deployment)
                else:
                    await self.docker.preflight(deployment)
                self.jobs.transition(job_id, "deploying")
                if deployment.provision_managed_resources:
                    await self.docker.prepare_resources(deployment)
                await self.docker.install(deployment)
                self.jobs.transition(job_id, "verifying")
                deadline = asyncio.get_running_loop().time() + (
                    deployment.readiness_timeout_seconds
                )
                while True:
                    state = await self.docker.readiness(deployment)
                    if state == "ready":
                        break
                    if state in (
                        "stopped", "not_deployed", "unhealthy",
                        "missing_healthcheck", "unmanaged_container_collision",
                    ):
                        raise ControllerError(
                            "Readiness failed: " + state
                        )
                    if asyncio.get_running_loop().time() >= deadline:
                        raise ControllerError("Readiness exceeded approved timeout")
                    await asyncio.sleep(1)
                self.jobs.set_desired(
                    deployment.addon_id, deployment.version, deployment.image, "running"
                )
                self.jobs.transition(job_id, "succeeded", "Readiness verified")
            elif action == "remove":
                self.jobs.transition(job_id, "removing")
                await self.docker.remove(deployment)
                self.jobs.set_desired(
                    deployment.addon_id, deployment.version, deployment.image, "absent"
                )
                self.jobs.transition(job_id, "succeeded", "Container removed")
            else:
                raise ControllerError("Unknown operation")
        except asyncio.CancelledError:
            self.jobs.transition(
                job_id, "interrupted", "Task stopped; verify host before resubmitting"
            )
            raise
        except (ControllerError, DockerError, OSError) as exc:
            message = str(exc)[:220]
            # Roll back only containers created by this install operation.
            # Removal failure must never trigger another destructive action.
            if (
                job["action"] == "install"
                and self.jobs.get(job_id)["status"] == "verifying"
                and deployment is not None
                and deployment.rollback_on_failed_readiness
            ):
                try:
                    self.jobs.transition(job_id, "rolling_back", "Readiness failed")
                    await self.docker.remove(deployment)
                    message += "; new container removed"
                except (DockerError, OSError):
                    self.jobs.transition(
                        job_id, "interrupted",
                        "Rollback not verified; administrator recovery required",
                    )
                    return
            self.jobs.transition(job_id, "failed", message)
        except Exception:
            # Never disclose secrets or host exception data to browser clients.
            self.jobs.transition(job_id, "failed", "Unexpected controller error")


    async def observed(self, addon_id: str) -> str:
        if addon_id not in self.approved():
            raise ControllerError("Add-on is not approved on this host")
        try:
            state = await self.docker.status(addon_id)
        except DockerError as exc:
            raise ControllerError("Could not verify Docker state") from exc
        if state not in ("running", "stopped", "not_deployed", "unmanaged_container_collision"):
            raise ControllerError("Unexpected Docker state")
        return state

    async def acknowledge(self, job_id: str, observed_state: str) -> dict:
        """Require an operator-approved, freshly rechecked observation."""
        async with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] != "interrupted":
                raise ControllerError("Job is not awaiting recovery review")
            actual = await self.observed(job["addon_id"])
            if actual == "unmanaged_container_collision":
                raise ControllerError("Cannot acknowledge an unowned Docker container")
            if actual != observed_state:
                raise ControllerError("Container state changed; review it again")
            try:
                return self.jobs.acknowledge(job_id, actual)
            except JobConflict as exc:
                raise ControllerError(str(exc)) from exc


    async def inventory(self) -> list[dict]:
        """Report expected vs observed status without mutating the host."""
        rows = []
        for item in self.jobs.desired():
            try:
                observed = await self.observed(item["addon_id"])
            except ControllerError:
                observed = "unavailable"
            expected = (
                "running" if item["desired_state"] == "running" else "not_deployed"
            )
            rows.append({
                **item,
                "observed_state": observed,
                "drift": observed != expected,
            })
        return rows
