"""Routing and deployment state models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RouteState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DRAINING = "draining"


class DeploymentState(StrEnum):
    REGISTERED = "registered"
    VALIDATING = "validating"
    QUEUED = "queued"
    LOADING = "loading"
    READY = "ready"
    DRAINING = "draining"
    UNLOADING = "unloading"
    STOPPED = "stopped"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


# States in which a new mutating lifecycle operation must not start.
BUSY_LIFECYCLE_STATES = frozenset(
    {
        DeploymentState.VALIDATING,
        DeploymentState.QUEUED,
        DeploymentState.LOADING,
        DeploymentState.DRAINING,
        DeploymentState.UNLOADING,
    }
)


class ModelAliasRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str
    deployment_id: str
    state: RouteState = RouteState.ENABLED
    priority: int = 100
    experimental: bool = False


class ModelDefinitionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    description: str = ""
    manifest: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    validation_status: str = "registered"
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeploymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    model_definition_id: str
    worker_id: str
    provider: str
    status: DeploymentState = DeploymentState.REGISTERED
    gpu_assignments: list[int] = Field(default_factory=list)
    runtime_configuration: dict[str, Any] = Field(default_factory=dict)
    active_requests: int = 0
    queue_depth: int = 0
    load_progress_percent: float | None = None
    loaded_at: datetime | None = None
    last_released_at: datetime | None = None
    last_successful_load: datetime | None = None
    last_error: str | None = None
    failure_reason: str | None = None
    request_count: int = 0
    maintenance: bool = False
    logs: list[str] = Field(default_factory=list)

    @property
    def locked(self) -> bool:
        return bool((self.runtime_configuration or {}).get("locked"))
