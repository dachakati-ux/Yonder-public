"""Generation (image/video) provider and job contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from yonder_contracts.inference import ProviderHealth


class GenerationMode(StrEnum):
    TEXT_TO_IMAGE = "txt2img"
    IMAGE_TO_IMAGE = "img2img"
    TEXT_TO_VIDEO = "txt2video"
    IMAGE_TO_VIDEO = "image2video"


class GenerationJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    NOT_SUPPORTED = "not_supported"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    REFERENCE = "reference"


@dataclass(frozen=True)
class GenerationRequest:
    request_id: str
    model_alias: str
    deployment_id: str
    mode: GenerationMode
    prompt: str
    negative_prompt: str = ""
    aspect_ratio: str = "1:1"
    style_preset: str | None = None
    steps: int = 20
    guidance: float = 7.5
    strength: float | None = None
    seed: int | None = None
    reference_artifact_ids: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationProgress:
    job_id: str
    step: str
    message: str
    percent: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationAssetRef:
    artifact_id: str
    kind: MediaKind
    mime_type: str
    url: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class GenerationJob:
    job_id: str
    request_id: str
    status: GenerationJobStatus
    mode: GenerationMode
    model_alias: str
    deployment_id: str
    provider: str
    availability: str
    progress: tuple[GenerationProgress, ...] = ()
    assets: tuple[GenerationAssetRef, ...] = ()
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class GenerationProvider(Protocol):
    """Adapter boundary for image/video generation runtimes."""

    name: str

    async def health(self) -> ProviderHealth: ...

    async def create_job(self, request: GenerationRequest) -> GenerationJob: ...

    async def get_job(self, job_id: str) -> GenerationJob: ...

    async def cancel_job(self, job_id: str) -> GenerationJob: ...
