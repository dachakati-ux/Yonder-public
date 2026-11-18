"""Controller orchestration. Only exact operator-approved deployment plans run."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from pydantic import ValidationError

from .docker_adapter import DockerAdapter, DockerError
from .models import ApprovedDeployment, ControllerConfig
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

    def approved(self) -> dict[str, ApprovedDeployment]:
        return load_deployments(self.config)

    def plan(self, addon_id: str, action: str) -> dict:
        if action not in ("install", "remove"):
            raise ControllerError("Unsupported action")
        selected = self.approved().get(addon_id)
        if selected is None:
            raise ControllerError("Add-on is not approved for deployment on this host")
        return selected.plan(action)

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
        try:
            self.jobs.transition(job_id, "preflight")
            action = job["action"]
            deployment = self.approved().get(job["addon_id"])
            if deployment is None:
                raise ControllerError("Approved deployment is no longer available")
            if deployment.plan(action)["plan_sha256"] != job["plan_sha256"]:
                raise ControllerError("Deployment changed before execution")
            if action == "install":
                await self.docker.preflight(deployment)
                self.jobs.transition(job_id, "deploying")
                await self.docker.install(deployment)
                self.jobs.transition(job_id, "verifying")
                if await self.docker.status(deployment.addon_id) != "running":
                    raise ControllerError("Deployment failed its container health check")
                self.jobs.transition(job_id, "succeeded", "Container is running")
            elif action == "remove":
                self.jobs.transition(job_id, "removing")
                await self.docker.remove(deployment)
                self.jobs.transition(job_id, "succeeded", "Container removed")
            else:
                raise ControllerError("Unknown operation")
        except asyncio.CancelledError:
            self.jobs.transition(
                job_id, "interrupted", "Task stopped; verify host before resubmitting"
            )
            raise
        except (ControllerError, DockerError, OSError) as exc:
            self.jobs.transition(job_id, "failed", str(exc)[:300])
        except Exception:
            # Never disclose secrets or host exception data to browser clients.
            self.jobs.transition(job_id, "failed", "Unexpected controller error")
