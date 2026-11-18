"""Strict, non-executable deployment configuration for approved OCI images."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ADDON = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
NON_SECRET_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SENSITIVE_KEY = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PRIVATE|CREDENTIAL|API_KEY|AUTH_KEY)", re.IGNORECASE
)
NETWORK_NAME = re.compile(r"^yonder-addon-net-[a-z][a-z0-9-]{1,63}$")
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
    network: str = "none"
    public_settings: dict[str, str] = Field(default_factory=dict, max_length=24)
    secret_volume: bool = False
    data_volume: bool = False
    provision_managed_resources: bool = False
    image_acquisition: Literal["preloaded", "signed_pull"] = "preloaded"
    readiness: Literal["running", "docker_healthy"] = "running"
    readiness_timeout_seconds: int = Field(default=30, ge=5, le=300)
    rollback_on_failed_readiness: bool = True

    @field_validator("network")
    @classmethod
    def permitted_network_name(cls, value: str) -> str:
        if value != "none" and not NETWORK_NAME.fullmatch(value):
            raise ValueError("Only a pre-provisioned Yonder add-on network is allowed")
        return value

    @field_validator("public_settings")
    @classmethod
    def nonsecret_settings(cls, value: dict[str, str]) -> dict[str, str]:
        for key, text in value.items():
            if (
                not NON_SECRET_KEY.fullmatch(key)
                or SENSITIVE_KEY.search(key)
                or len(text) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in text)
            ):
                raise ValueError("Public settings cannot contain secrets or control characters")
        return value

    @model_validator(mode="after")
    def digest_only(self) -> ApprovedDeployment:
        if self.network != "none" and self.network != f"yonder-addon-net-{self.addon_id}":
            raise ValueError("Network must be dedicated to the selected add-on")
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
            "configuration": {
                "provision_managed_resources": self.provision_managed_resources,
                "public_settings": self.public_settings,
                "secret_volume": f"yonder-addon-secrets-{self.addon_id}"
                if self.secret_volume else None,
                "data_volume": f"yonder-addon-data-{self.addon_id}"
                if self.data_volume else None,
            },
            "readiness": {
                "mode": self.readiness,
                "timeout_seconds": self.readiness_timeout_seconds,
                "rollback_on_failure": self.rollback_on_failed_readiness,
            },
            "requires_approval": True,
            "note": (
                "Only the pre-approved digest is used. "
                "Named volumes must be operator-provisioned and verified. "
                "No host paths, ports, privileged mode or arbitrary commands."
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
