"""Hardware and metric contracts with explicit availability states."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MetricAvailability(StrEnum):
    """Explicit states for values that must never be fabricated."""

    AVAILABLE = "available"
    NOT_SUPPORTED = "not_supported"
    NOT_REPORTED = "not_reported"
    PERMISSION_UNAVAILABLE = "permission_unavailable"
    WORKER_DISCONNECTED = "worker_disconnected"
    COLLECTION_FAILED = "collection_failed"


class MetricValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: MetricAvailability
    value: float | int | str | bool | None = None
    unit: str | None = None
    message: str | None = None

    @classmethod
    def available(
        cls,
        value: float | int | str | bool,
        *,
        unit: str | None = None,
    ) -> MetricValue:
        return cls(availability=MetricAvailability.AVAILABLE, value=value, unit=unit)

    @classmethod
    def unavailable(
        cls,
        availability: MetricAvailability,
        *,
        message: str | None = None,
        unit: str | None = None,
    ) -> MetricValue:
        if availability == MetricAvailability.AVAILABLE:
            raise ValueError("Use MetricValue.available() for available metrics")
        return cls(availability=availability, value=None, unit=unit, message=message)


class GpuMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: MetricValue
    uuid: MetricValue
    name: MetricValue
    utilization_percent: MetricValue
    memory_used_bytes: MetricValue
    memory_total_bytes: MetricValue
    memory_available_bytes: MetricValue
    temperature_c: MetricValue
    power_draw_watts: MetricValue
    power_limit_watts: MetricValue
    clock_graphics_mhz: MetricValue
    clock_memory_mhz: MetricValue
    ecc_status: MetricValue
    nvlink_state: MetricValue
    active_processes: MetricValue


class StorageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mountpoint: str
    total_bytes: MetricValue
    used_bytes: MetricValue
    available_bytes: MetricValue
    health: MetricValue


class NetworkInterfaceMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    is_up: MetricValue
    addresses: MetricValue
    bytes_sent: MetricValue
    bytes_recv: MetricValue
    throughput_sent_bps: MetricValue
    throughput_recv_bps: MetricValue


class HostMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: MetricValue
    operating_system: MetricValue
    kernel_version: MetricValue
    architecture: MetricValue
    cpu_model: MetricValue
    cpu_count: MetricValue
    cpu_utilization_percent: MetricValue
    memory_total_bytes: MetricValue
    memory_used_bytes: MetricValue
    memory_available_bytes: MetricValue
    uptime_seconds: MetricValue
    nvidia_driver_version: MetricValue
    cuda_version: MetricValue
    storage: list[StorageMetrics] = Field(default_factory=list)
    network_interfaces: list[NetworkInterfaceMetrics] = Field(default_factory=list)


class WorkerRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    ready: bool
    maintenance: bool = False
    runtime_health: MetricValue
    loaded_deployments: list[str] = Field(default_factory=list)
    active_requests: int = 0
    queue_depth: int = 0
    last_heartbeat: datetime | None = None
    last_error: str | None = None
    agent_version: str


class HardwareInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collected_at: datetime
    host: HostMetrics
    gpus: list[GpuMetrics] = Field(default_factory=list)
    gpu_discovery_status: MetricAvailability
    gpu_discovery_message: str | None = None
    notes: list[str] = Field(default_factory=list)
    raw_collector_errors: list[str] = Field(default_factory=list)

    def model_dump_public(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
