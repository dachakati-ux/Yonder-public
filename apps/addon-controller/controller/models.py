"""Strict, non-executable deployment configuration for approved OCI images."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ADDON = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
DIGEST_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]*(?::[a-z0-9._-]+)?@sha256:[a-f0-9]{64}$"
)


class ApprovedDeployment(BaseModel):
    """Operator-reviewed deployment data; no shell, mounts or commands allowed."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str = Field(pattern=ADDON.pattern)
    image: str = Field(max_length=256)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    memory_mb: int = Field(default=512, ge=128, le=8192)
    cpus: float = Field(default=1.0, ge=0.25, le=4)
    pids_limit: int = Field(default=128, ge=32, le=512)
    network: Literal["none"] = "none"
    image_acquisition: Literal["preloaded", "signed_pull"] = "preloaded"
    readiness: Literal["running", "docker_healthy"] = "running"
    readiness_timeout_seconds: int = Field(default=30, ge=5, le=300)
    rollback_on_failed_readiness: bool = True

    @model_validator(mode="after")
    def digest_only(self) -> ApprovedDeployment:
        if not DIGEST_IMAGE.fullmatch(self.image):
            raise ValueError("OCI image must be pinned to a full SHA-256 digest")
        return self

    def plan(self, action: Literal["install", "remove"]) -> dict[str, object]:
        base: dict[str, object] = {
            "schema_version": 1,
            "addon_id": self.addon_id,
            "version": self.version,
            "action": action,
            "adapter": "docker",
            "image": self.image,
            "image_acquisition": self.image_acquisition,
            "limits": {
                "memory_mb": self.memory_mb,
                "cpus": self.cpus,
                "pids": self.pids_limit,
                "network": self.network,
            },
            "readiness": {
                "mode": self.readiness,
                "timeout_seconds": self.readiness_timeout_seconds,
                "rollback_on_failure": self.rollback_on_failed_readiness,
            },
            "requires_approval": True,
            "note": (
                "Only the pre-approved digest is used. "
                "No host mounts, ports, privileged mode or arbitrary commands."
            ),
        }
        serialized = json.dumps(base, sort_keys=True, separators=(",", ":")).encode()
        base["plan_sha256"] = hashlib.sha256(serialized).hexdigest()
        return base


class ControllerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    deployments: list[ApprovedDeployment] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def no_duplicates(self) -> ControllerConfig:
        ids = [item.addon_id for item in self.deployments]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate deployment ID")
        return self
