"""Gateway speech orchestration — worker STT/TTS only, fail closed."""

from __future__ import annotations

from typing import Any

import httpx
from yonder_configuration.worker_auth import worker_auth_headers
from yonder_contracts.capabilities import CapabilityAvailability
from yonder_contracts.speech import (
    SynthesizeRequest,
    SynthesizeResult,
    TranscribeRequest,
    TranscribeResult,
)

from app.services.artifacts import LocalArtifactStore


class SpeechServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        availability: CapabilityAvailability = CapabilityAvailability.NOT_SUPPORTED,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.availability = availability
        self.details = details or {}


class SpeechService:
    def __init__(
        self,
        *,
        enabled: bool,
        worker_base_url: str,
        timeout_seconds: float,
        artifact_store: LocalArtifactStore,
        stt_alias: str = "yonder-stt",
        tts_alias: str = "yonder-tts",
    ) -> None:
        self.enabled = enabled
        self.worker_base_url = worker_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.artifact_store = artifact_store
        self.stt_alias = stt_alias
        self.tts_alias = tts_alias

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "worker_base_url": self.worker_base_url,
            "stt_alias": self.stt_alias,
            "tts_alias": self.tts_alias,
            "availability": (
                CapabilityAvailability.NOT_SUPPORTED.value
                if not self.enabled
                else CapabilityAvailability.NOT_REPORTED.value
            ),
        }

    async def probe_status(self) -> dict[str, Any]:
        base = self.status()
        if not self.enabled:
            return base
        async with httpx.AsyncClient(
            timeout=min(self.timeout_seconds, 10.0), headers=worker_auth_headers()
        ) as client:
            try:
                response = await client.get(f"{self.worker_base_url}/v1/speech/status")
            except httpx.HTTPError as exc:
                return {
                    **base,
                    "availability": CapabilityAvailability.NOT_REPORTED.value,
                    "message": f"Speech worker unreachable: {exc}",
                }
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            return {
                **base,
                "availability": CapabilityAvailability.NOT_REPORTED.value,
                "message": str(body.get("message") or response.text),
                "worker": body if isinstance(body, dict) else {},
            }
        return {
            **base,
            "availability": str(
                body.get("availability") or CapabilityAvailability.NOT_REPORTED.value
            ),
            "worker": body if isinstance(body, dict) else {},
        }

    async def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        if not self.enabled:
            raise SpeechServiceError(
                "not_supported",
                "Speech runtime is not configured on this Gateway.",
                availability=CapabilityAvailability.NOT_SUPPORTED,
            )
        params: dict[str, str] = {"request_id": request.request_id}
        if request.language:
            params["language"] = request.language
        headers = {
            "Content-Type": request.mime_type or "application/octet-stream",
            "X-Yonder-Filename": request.filename or "audio.webm",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=worker_auth_headers()
        ) as client:
            try:
                response = await client.post(
                    f"{self.worker_base_url}/v1/speech/transcribe",
                    content=request.audio,
                    params=params,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise SpeechServiceError(
                    "provider_failure",
                    f"Speech worker unreachable: {exc}",
                    availability=CapabilityAvailability.NOT_REPORTED,
                ) from exc
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            payload = detail if isinstance(detail, dict) else body
            if not isinstance(payload, dict):
                payload = {}
            avail = str(payload.get("availability") or "not_supported")
            try:
                availability = CapabilityAvailability(avail)
            except ValueError:
                availability = CapabilityAvailability.NOT_SUPPORTED
            raise SpeechServiceError(
                str(payload.get("code") or "provider_failure"),
                str(payload.get("message") or response.text or "transcribe failed"),
                availability=availability,
                details=payload,
            )
        availability_raw = str(body.get("availability") or "ready")
        try:
            availability = CapabilityAvailability(availability_raw)
        except ValueError:
            availability = CapabilityAvailability.NOT_REPORTED
        return TranscribeResult(
            text=str(body.get("text") or ""),
            availability=availability,
            language=body.get("language"),
            duration_seconds=body.get("duration_seconds"),
            deployment_id=body.get("deployment_id"),
            message=body.get("message"),
        )

    async def synthesize(self, request: SynthesizeRequest) -> SynthesizeResult:
        if not self.enabled:
            raise SpeechServiceError(
                "not_supported",
                "Speech runtime is not configured on this Gateway.",
                availability=CapabilityAvailability.NOT_SUPPORTED,
            )
        payload = {
            "request_id": request.request_id,
            "text": request.text,
            "voice": request.voice,
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=worker_auth_headers()
        ) as client:
            try:
                response = await client.post(
                    f"{self.worker_base_url}/v1/speech/synthesize",
                    json=payload,
                )
            except httpx.HTTPError as exc:
                raise SpeechServiceError(
                    "provider_failure",
                    f"Speech worker unreachable: {exc}",
                    availability=CapabilityAvailability.NOT_REPORTED,
                ) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = {}
            detail = body.get("detail") if isinstance(body, dict) else None
            payload = detail if isinstance(detail, dict) else body
            if not isinstance(payload, dict):
                payload = {}
            avail = str(payload.get("availability") or "not_supported")
            try:
                availability = CapabilityAvailability(avail)
            except ValueError:
                availability = CapabilityAvailability.NOT_SUPPORTED
            raise SpeechServiceError(
                str(payload.get("code") or "provider_failure"),
                str(payload.get("message") or response.text or "synthesize failed"),
                availability=availability,
                details=payload,
            )
        mime = response.headers.get("content-type") or "audio/wav"
        if ";" in mime:
            mime = mime.split(";", 1)[0].strip()
        audio = response.content
        if not audio:
            raise SpeechServiceError(
                "provider_failure",
                "Speech worker returned empty audio.",
                availability=CapabilityAvailability.NOT_REPORTED,
            )
        stored = self.artifact_store.put(
            data=audio,
            filename="speech.wav" if "wav" in mime else "speech.audio",
            mime_type=mime if mime in {
                "audio/wav",
                "audio/wave",
                "audio/x-wav",
                "audio/mpeg",
                "audio/mp3",
                "audio/ogg",
                "audio/webm",
            } else "audio/wav",
            kind="speech",
        )
        return SynthesizeResult(
            audio=audio,
            mime_type=stored.mime_type,
            availability=CapabilityAvailability.READY,
            deployment_id=response.headers.get("x-yonder-deployment-id"),
            message=None,
            artifact_id=stored.artifact_id,
            url=f"/v1/speech/audio/{stored.artifact_id}",
            storage_key=stored.storage_key,
        )
