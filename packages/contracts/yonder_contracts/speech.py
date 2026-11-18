"""Speech (STT/TTS) contracts — Gateway-mediated only."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from yonder_contracts.capabilities import CapabilityAvailability
from yonder_contracts.inference import ProviderHealth


class SpeechKind(StrEnum):
    TRANSCRIBE = "transcribe"
    SYNTHESIZE = "synthesize"


@dataclass(frozen=True)
class TranscribeRequest:
    request_id: str
    audio: bytes
    mime_type: str
    filename: str = "audio.webm"
    language: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscribeResult:
    text: str
    availability: CapabilityAvailability
    language: str | None = None
    duration_seconds: float | None = None
    deployment_id: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class SynthesizeRequest:
    request_id: str
    text: str
    voice: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SynthesizeResult:
    audio: bytes
    mime_type: str
    availability: CapabilityAvailability
    deployment_id: str | None = None
    message: str | None = None
    artifact_id: str | None = None
    url: str | None = None
    storage_key: str | None = None


class SpeechProvider(Protocol):
    """Worker-backed speech provider used by the Gateway."""

    name: str

    async def health(self) -> ProviderHealth: ...

    async def transcribe(self, request: TranscribeRequest) -> TranscribeResult: ...

    async def synthesize(self, request: SynthesizeRequest) -> SynthesizeResult: ...

    def status(self) -> dict[str, Any]: ...
