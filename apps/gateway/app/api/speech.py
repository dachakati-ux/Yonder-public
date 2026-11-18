"""Gateway-only speech APIs (STT/TTS)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode
from yonder_contracts.speech import SynthesizeRequest, TranscribeRequest

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.services.speech import SpeechServiceError

router = APIRouter(prefix="/v1/speech", tags=["speech"])

MAX_AUDIO_BYTES = 25 * 1024 * 1024


class SynthesizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)
    voice: str | None = None


def _speech(request: Request):
    return getattr(request.app.state, "speech", None)


def _require_speech_capability(principal: Any, request_id: str):
    if principal.has_capability("speech.use") or principal.has_capability("chat.use"):
        return None
    return error_response(
        ErrorCode.PERMISSION_CONTEXT_REQUIRED,
        "Principal lacks speech.use capability.",
        request_id,
    )


@router.get("/status")
async def speech_status(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_speech_capability(principal, request_id)
    if denied:
        return denied
    service = _speech(request)
    if service is None:
        return {
            "enabled": False,
            "availability": "not_supported",
            "message": "Speech service is not wired on this Gateway.",
        }
    return {"request_id": request_id, **(await service.probe_status())}


@router.post("/transcribe")
async def transcribe(
    request: Request,
    file: Annotated[UploadFile, File()],
    language: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_speech_capability(principal, request_id)
    if denied:
        return denied
    service = _speech(request)
    if service is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Speech service is not wired on this Gateway.",
            request_id,
            status_code=501,
            details={"availability": "not_supported"},
        )
    data = await file.read()
    if len(data) > MAX_AUDIO_BYTES:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Audio upload exceeds 25 MiB limit.",
            request_id,
            status_code=400,
        )
    if not data:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Audio upload is empty.",
            request_id,
            status_code=400,
        )
    mime = file.content_type or "application/octet-stream"
    try:
        result = await service.transcribe(
            TranscribeRequest(
                request_id=request_id,
                audio=data,
                mime_type=mime,
                filename=file.filename or "audio.webm",
                language=language,
            )
        )
    except SpeechServiceError as exc:
        unavailable = exc.availability.value in {
            "not_supported",
            "not_configured",
            "not_ready",
        }
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE if unavailable else ErrorCode.PROVIDER_FAILURE,
            exc.message,
            request_id,
            status_code=501 if unavailable else 502,
            details={
                "availability": exc.availability.value,
                **exc.details,
            },
        )
    return {
        "request_id": request_id,
        "text": result.text,
        "language": result.language,
        "duration_seconds": result.duration_seconds,
        "availability": result.availability.value,
        "deployment_id": result.deployment_id,
        "message": result.message,
    }


@router.post("/synthesize")
async def synthesize(body: SynthesizeBody, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_speech_capability(principal, request_id)
    if denied:
        return denied
    service = _speech(request)
    if service is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Speech service is not wired on this Gateway.",
            request_id,
            status_code=501,
            details={"availability": "not_supported"},
        )
    try:
        result = await service.synthesize(
            SynthesizeRequest(
                request_id=request_id,
                text=body.text,
                voice=body.voice,
            )
        )
    except SpeechServiceError as exc:
        unavailable = exc.availability.value in {
            "not_supported",
            "not_configured",
            "not_ready",
        }
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE if unavailable else ErrorCode.PROVIDER_FAILURE,
            exc.message,
            request_id,
            status_code=501 if unavailable else 502,
            details={
                "availability": exc.availability.value,
                **exc.details,
            },
        )
    meta = {
        "id": result.artifact_id,
        "principal_subject": principal.subject,
        "kind": "speech",
        "mime_type": result.mime_type,
        "storage_key": result.storage_key,
        "url": result.url,
    }
    cache = getattr(request.app.state, "speech_audio_cache", None)
    if cache is None:
        request.app.state.speech_audio_cache = {}
        cache = request.app.state.speech_audio_cache
    if result.artifact_id:
        cache[result.artifact_id] = meta
    return {
        "request_id": request_id,
        "availability": result.availability.value,
        "deployment_id": result.deployment_id,
        "mime_type": result.mime_type,
        "artifact_id": result.artifact_id,
        "url": result.url,
    }


@router.get("/audio/{artifact_id}")
async def get_speech_audio(artifact_id: str, request: Request) -> Response:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_speech_capability(principal, request_id)
    if denied:
        return denied
    cache = getattr(request.app.state, "speech_audio_cache", {}) or {}
    meta = cache.get(artifact_id)
    if meta is None or meta.get("principal_subject") != principal.subject:
        return error_response(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Artifact not found or not visible.",
            request_id,
            status_code=404,
            details={"artifact_id": artifact_id},
        )
    storage_key = meta.get("storage_key")
    if not storage_key:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Artifact bytes missing from store.",
            request_id,
            status_code=404,
        )
    try:
        data = request.app.state.artifact_store.read(storage_key)
    except FileNotFoundError:
        return error_response(
            ErrorCode.INVALID_REQUEST,
            "Artifact bytes missing from store.",
            request_id,
            status_code=404,
        )
    return Response(
        content=data,
        media_type=meta.get("mime_type") or "audio/wav",
        headers={"Cache-Control": "private, max-age=300"},
    )
