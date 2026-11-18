"""Worker-local speech endpoints (STT/TTS). Gateway is the only client.

Transcribe uses a raw request body (not multipart) so air-gapped workers do not
require python-multipart at import time.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/v1/speech", tags=["speech"])

MAX_AUDIO_BYTES = 25 * 1024 * 1024


class SynthesizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    text: str = Field(min_length=1, max_length=8000)
    voice: str | None = None


def _manager(request: Request):
    return getattr(request.app.state, "speech_runtime", None)


@router.get("/status")
async def speech_status(request: Request) -> dict[str, Any]:
    manager = _manager(request)
    if manager is None:
        return {
            "availability": "not_supported",
            "enabled": False,
            "message": "Speech runtime not wired on this worker",
            "worker_id": request.app.state.settings.worker_id,
        }
    return {
        **manager.status(),
        "worker_id": request.app.state.settings.worker_id,
    }


@router.post("/transcribe")
async def transcribe(request: Request) -> dict[str, Any]:
    manager = _manager(request)
    if manager is None:
        return {
            "text": "",
            "availability": "not_supported",
            "code": "not_supported",
            "message": "Speech runtime not wired on this worker",
        }
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Audio upload is empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="Audio upload exceeds 25 MiB")
    filename = request.headers.get("x-yonder-filename") or "audio.webm"
    language = request.query_params.get("language")
    result = manager.transcribe(
        audio=data,
        filename=filename,
        language=language,
    )
    if result.get("code") in {"not_supported", "not_configured"}:
        raise HTTPException(status_code=501, detail=result)
    return {**result, "worker_id": request.app.state.settings.worker_id}


@router.post("/synthesize")
async def synthesize(body: SynthesizeBody, request: Request) -> Response:
    manager = _manager(request)
    if manager is None:
        raise HTTPException(
            status_code=501,
            detail={
                "availability": "not_supported",
                "code": "not_supported",
                "message": "Speech runtime not wired on this worker",
            },
        )
    audio, mime, meta = manager.synthesize(text=body.text, voice=body.voice)
    if meta.get("code") in {"not_supported", "not_configured", "provider_failure"}:
        raise HTTPException(
            status_code=501 if meta.get("code") != "provider_failure" else 502,
            detail=meta,
        )
    if not audio:
        raise HTTPException(
            status_code=502,
            detail={
                "availability": "not_reported",
                "code": "provider_failure",
                "message": meta.get("message") or "Empty audio",
            },
        )
    headers = {
        "X-Yonder-Availability": str(meta.get("availability") or "ready"),
    }
    if meta.get("deployment_id"):
        headers["X-Yonder-Deployment-Id"] = str(meta["deployment_id"])
    return Response(content=audio, media_type=mime, headers=headers)
