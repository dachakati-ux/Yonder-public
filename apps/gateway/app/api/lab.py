"""Training Lab HTTP API — gateway-mediated teaching demos + Academy."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import raise_error
from app.services.curriculum import curriculum_public_dict, get_curriculum, lesson_public_dict
from app.services.lab_live import alias_is_ready, load_runtime_config, run_ability_live_or_mock
from app.services.lab_scenarios import (
    list_abilities,
    list_generation_presets,
)
from app.services.missions import (
    evaluate_mission_check,
    get_missions,
    get_prompt_library,
    mission_public_dict,
    missions_public_list,
)
from app.services.progress import progress_overlay, track_completion_stats
from app.services.progress_events import apply_progress_event
from app.services.sandbox import PRESET_LABELS, SandboxQuotaError

router = APIRouter(prefix="/v1/lab", tags=["lab"])

AbilityId = Literal["tools", "rag", "structured", "guided", "generation"]
GuidedMode = Literal["summarize", "critique", "explain", "coach"]
MediaKind = Literal["image", "video"]
GenerationMode = Literal["txt2img", "img2img", "txt2video", "image2video"]
AspectRatio = Literal["1:1", "16:9", "9:16", "4:3", "3:4"]
StylePreset = Literal["yonder-brand", "photoreal", "illustration", "none"]
ProgressEventType = Literal["start", "check", "try_it", "complete"]


class GenerationOptions(BaseModel):
    model_config = {"extra": "forbid"}

    media_kind: MediaKind | None = None
    mode: GenerationMode = "txt2img"
    aspect_ratio: AspectRatio = "16:9"
    negative_prompt: str | None = Field(default=None, max_length=2000)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    steps: int = Field(default=20, ge=1, le=150)
    guidance: float = Field(default=7.0, ge=0.0, le=30.0)
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    style_preset: StylePreset = "none"
    reference_note: str | None = Field(default=None, max_length=2000)


class LabRunRequest(BaseModel):
    model_config = {"extra": "forbid"}

    ability: AbilityId
    input: str = Field(min_length=1, max_length=8000)
    guided_mode: GuidedMode | None = None
    media_kind: MediaKind | None = None
    generation: GenerationOptions | None = None


class LabRunResponse(BaseModel):
    model_config = {"extra": "forbid"}

    ability: str
    request_id: str
    mode: str
    provider: str
    steps: list[dict[str, Any]]
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    structured: dict[str, Any] | None = None
    assets: list[dict[str, Any]] = Field(default_factory=list)
    teaching: dict[str, list[str]]
    guided_mode: str | None = None
    media_kind: str | None = None


class ProgressEventRequest(BaseModel):
    model_config = {"extra": "forbid"}

    event: ProgressEventType
    selected_option_id: str | None = None
    answer: str | None = Field(default=None, max_length=8000)
    request_id: str | None = None


class SandboxCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    label: str = Field(default="Practice desk", max_length=120)
    preset: str | None = Field(default=None, max_length=64)


class SandboxDocumentRequest(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20000)


class SandboxRetrieveRequest(BaseModel):
    model_config = {"extra": "forbid"}

    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=3, ge=1, le=8)


class SandboxPartRequest(BaseModel):
    model_config = {"extra": "forbid"}

    part_number: str = Field(min_length=1, max_length=128)
    quantity: int = Field(default=1, ge=1, le=1000)
    incident_id: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=1000)


class MissionEventRequest(BaseModel):
    model_config = {"extra": "forbid"}

    event: Literal["start", "step", "check"]
    step_id: str | None = None
    answer: str | None = Field(default=None, max_length=8000)


def _require_lab(principal, request_id: str) -> None:
    if not principal.has_capability("lab.use"):
        raise_error(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks lab.use capability.",
            request_id,
        )


@router.get("/abilities")
async def lab_abilities(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    cfg = load_runtime_config()
    chat_alias = str(cfg["chat_alias"])
    live_ready = alias_is_ready(request, chat_alias)
    return {
        "abilities": list_abilities(),
        "mode": "live" if live_ready else "training_scenario",
        "chat_alias": chat_alias,
        "live_ready": live_ready,
        "note": (
            f"Lab prefers live inference on alias `{chat_alias}` when READY. "
            "Otherwise demos fall back to labeled training_scenario mocks. "
            "Generation stays mock until a generation worker exists."
        ),
    }


@router.get("/generation/presets")
async def lab_generation_presets(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    return list_generation_presets()


@router.get("/curriculum")
async def lab_curriculum(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    curriculum = get_curriculum()
    body = curriculum_public_dict(curriculum)
    rows = await request.app.state.progress.list_progress(principal.subject)
    all_ids = [lid for track in curriculum.tracks for lid in track.lesson_ids]
    overlay = progress_overlay(lesson_ids=all_ids, rows=rows)
    tracks_out = []
    for track in body["tracks"]:
        stats = track_completion_stats(track["lesson_ids"], overlay)
        lessons = []
        for lesson in track["lessons"]:
            lessons.append({**lesson, "progress": overlay.get(lesson["id"])})
        tracks_out.append({**track, "lessons": lessons, "completion": stats})
    return {
        "version": body["version"],
        "tracks": tracks_out,
        "sandbox": body["sandbox"],
        "principal_subject": principal.subject,
        "progress": overlay,
    }


@router.get("/progress")
async def lab_progress(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    curriculum = get_curriculum()
    rows = await request.app.state.progress.list_progress(principal.subject)
    all_ids = [lid for track in curriculum.tracks for lid in track.lesson_ids]
    overlay = progress_overlay(lesson_ids=all_ids, rows=rows)
    tracks = {
        track.id: track_completion_stats(track.lesson_ids, overlay)
        for track in curriculum.tracks
    }
    return {
        "principal_subject": principal.subject,
        "lessons": overlay,
        "tracks": tracks,
    }


@router.get("/progress/{lesson_id}")
async def lab_progress_lesson(lesson_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    curriculum = get_curriculum()
    lesson = curriculum.lesson(lesson_id)
    if lesson is None:
        raise_error(ErrorCode.INVALID_REQUEST, f"Unknown lesson: {lesson_id}", request_id)
    rows = await request.app.state.progress.list_progress(principal.subject)
    overlay = progress_overlay(lesson_ids=[lesson_id], rows=rows)
    return {
        "lesson": lesson_public_dict(curriculum, lesson),
        "progress": overlay[lesson_id],
        "principal_subject": principal.subject,
    }


@router.post("/progress/{lesson_id}/events")
async def lab_progress_event(
    lesson_id: str, body: ProgressEventRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    curriculum = get_curriculum()
    if curriculum.lesson(lesson_id) is None:
        raise_error(ErrorCode.INVALID_REQUEST, f"Unknown lesson: {lesson_id}", request_id)
    try:
        result = await apply_progress_event(
            store=request.app.state.progress,
            curriculum=curriculum,
            principal_subject=principal.subject,
            lesson_id=lesson_id,
            event=body.event,
            payload={
                "selected_option_id": body.selected_option_id,
                "answer": body.answer,
                "request_id": body.request_id,
            },
            request_id=request_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "completion_requirements_not_met":
            raise_error(
                ErrorCode.INVALID_REQUEST,
                "Lesson completion requirements not met.",
                request_id,
            )
        raise_error(ErrorCode.INVALID_REQUEST, msg, request_id)
    return {
        "lesson_id": result.lesson_id,
        "status": result.status,
        "check_passed": result.check_passed,
        "try_it_recorded": result.try_it_recorded,
        "check_correct": result.check_correct,
        "explanation": result.explanation,
        "completed": result.completed,
        "progress": result.progress.model_dump(mode="json"),
        "next_lesson_id": curriculum.next_lesson_id(lesson_id),
    }


@router.post("/progress/reset")
async def lab_progress_reset(request: Request) -> dict[str, Any]:
    """Reset progress for the calling principal (demo / instructor aid)."""

    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    cleared = await request.app.state.progress.reset_progress(principal.subject)
    return {"principal_subject": principal.subject, "cleared": cleared}


@router.post("/sandbox")
async def lab_sandbox_create(body: SandboxCreateRequest, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    if body.preset and request.app.state.corpus_quarantine.is_quarantined(body.preset):
        raise_error(
            ErrorCode.INVALID_REQUEST,
            f"Sandbox preset '{body.preset}' is quarantined by an instructor.",
            request_id,
        )
    try:
        box = request.app.state.sandboxes.create(
            principal_subject=principal.subject,
            label=body.label,
            preset=body.preset,
        )
    except SandboxQuotaError as exc:
        raise_error(ErrorCode.INVALID_REQUEST, exc.message, request_id)
    return {"sandbox": box.model_dump()}


@router.get("/sandbox")
async def lab_sandbox_list(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    boxes = request.app.state.sandboxes.list_for_principal(principal.subject)
    return {"sandboxes": [b.model_dump() for b in boxes]}


@router.get("/sandbox/presets")
async def lab_sandbox_presets(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    return {
        "presets": [
            {"id": preset_id, "label": label}
            for preset_id, label in sorted(PRESET_LABELS.items())
        ]
    }


@router.get("/sandbox/{sandbox_id}")
async def lab_sandbox_get(sandbox_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    box = request.app.state.sandboxes.get(sandbox_id, principal.subject)
    if box is None:
        raise_error(ErrorCode.INVALID_REQUEST, "Sandbox not found.", request_id)
    return {"sandbox": box.model_dump()}


@router.post("/sandbox/{sandbox_id}/documents")
async def lab_sandbox_add_document(
    sandbox_id: str, body: SandboxDocumentRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    try:
        doc = request.app.state.sandboxes.add_document(
            sandbox_id,
            principal.subject,
            title=body.title,
            text=body.text,
        )
    except KeyError:
        raise_error(ErrorCode.INVALID_REQUEST, "Sandbox not found.", request_id)
    except SandboxQuotaError as exc:
        raise_error(ErrorCode.INVALID_REQUEST, exc.message, request_id)
    box = request.app.state.sandboxes.get(sandbox_id, principal.subject)
    return {"document": doc.model_dump(), "sandbox": box.model_dump() if box else None}


@router.post("/sandbox/{sandbox_id}/retrieve")
async def lab_sandbox_retrieve(
    sandbox_id: str, body: SandboxRetrieveRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    try:
        result = request.app.state.sandboxes.retrieve(
            sandbox_id, principal.subject, query=body.query, k=body.k
        )
    except KeyError:
        raise_error(ErrorCode.INVALID_REQUEST, "Sandbox not found.", request_id)
    except SandboxQuotaError as exc:
        raise_error(ErrorCode.INVALID_REQUEST, exc.message, request_id)
    return result


@router.post("/sandbox/{sandbox_id}/parts")
async def lab_sandbox_add_part(
    sandbox_id: str, body: SandboxPartRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    try:
        entry = request.app.state.sandboxes.add_part(
            sandbox_id,
            principal.subject,
            part_number=body.part_number,
            quantity=body.quantity,
            incident_id=body.incident_id,
            note=body.note,
        )
    except KeyError:
        raise_error(ErrorCode.INVALID_REQUEST, "Sandbox not found.", request_id)
    except SandboxQuotaError as exc:
        raise_error(ErrorCode.INVALID_REQUEST, exc.message, request_id)
    box = request.app.state.sandboxes.get(sandbox_id, principal.subject)
    return {"entry": entry.model_dump(), "sandbox": box.model_dump() if box else None}


@router.post("/sandbox/{sandbox_id}/reset")
async def lab_sandbox_reset(sandbox_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    box = request.app.state.sandboxes.reset(sandbox_id, principal.subject)
    if box is None:
        raise_error(ErrorCode.INVALID_REQUEST, "Sandbox not found.", request_id)
    return {"sandbox": box.model_dump()}


@router.get("/missions")
async def lab_missions(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    missions = get_missions()
    overlay = request.app.state.mission_progress.public_overlay(principal.subject)
    items = []
    for item in missions_public_list(missions):
        items.append({**item, "progress": overlay.get(item["id"])})
    return {"missions": items, "principal_subject": principal.subject}


@router.get("/missions/{mission_id}")
async def lab_mission_get(mission_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    mission = get_missions().get(mission_id)
    if mission is None:
        raise_error(ErrorCode.INVALID_REQUEST, f"Unknown mission: {mission_id}", request_id)
    progress = request.app.state.mission_progress.get(principal.subject, mission_id)
    return {
        "mission": mission_public_dict(mission),
        "progress": progress.model_dump() if progress else None,
    }


@router.post("/missions/{mission_id}/events")
async def lab_mission_event(
    mission_id: str, body: MissionEventRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    mission = get_missions().get(mission_id)
    if mission is None:
        raise_error(ErrorCode.INVALID_REQUEST, f"Unknown mission: {mission_id}", request_id)
    check_passed: bool | None = None
    explanation: str | None = None
    if body.event == "check":
        ok, explanation = evaluate_mission_check(mission, body.answer or "")
        check_passed = ok
    try:
        progress = request.app.state.mission_progress.apply_event(
            principal_subject=principal.subject,
            mission_id=mission_id,
            event=body.event,
            step_id=body.step_id,
            check_passed=check_passed,
        )
    except ValueError as exc:
        raise_error(ErrorCode.INVALID_REQUEST, str(exc), request_id)
    return {
        "mission_id": mission_id,
        "status": progress.status,
        "completed_steps": progress.completed_steps,
        "check_passed": progress.check_passed,
        "check_correct": check_passed,
        "explanation": explanation,
        "progress": progress.model_dump(),
    }


@router.get("/prompts")
async def lab_prompts(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    library = get_prompt_library()
    return {
        "version": library.version,
        "patterns": [
            p.model_dump()
            for p in library.patterns
            if p.status == "reviewed"
        ],
        "note": "Governed patterns only (status=reviewed). Drafts are not listed.",
    }


@router.post("/run", response_model=LabRunResponse)
async def lab_run(body: LabRunRequest, request: Request) -> LabRunResponse:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    if body.ability == "guided" and body.guided_mode is None:
        raise_error(
            ErrorCode.INVALID_REQUEST,
            "guided_mode is required when ability is guided.",
            request_id,
        )
    if body.ability == "generation" and body.media_kind is None and body.generation is None:
        raise_error(
            ErrorCode.INVALID_REQUEST,
            "media_kind or generation options are required when ability is generation.",
            request_id,
        )

    generation_payload = (
        body.generation.model_dump(exclude_none=True) if body.generation else None
    )
    media_kind = body.media_kind
    if generation_payload and generation_payload.get("media_kind"):
        media_kind = generation_payload["media_kind"]
    elif body.ability == "generation" and media_kind is None:
        mode = (generation_payload or {}).get("mode", "txt2img")
        media_kind = "video" if mode in {"txt2video", "image2video"} else "image"

    result = await run_ability_live_or_mock(
        request,
        request_id=request_id,
        ability=body.ability,
        user_input=body.input,
        guided_mode=body.guided_mode,
        media_kind=media_kind,
        generation=generation_payload,
    )
    return LabRunResponse(
        ability=str(result["ability"]),
        request_id=request_id,
        mode=str(result["mode"]),
        provider=str(result["provider"]),
        steps=list(result["steps"]),
        answer=str(result["answer"]),
        citations=list(result.get("citations") or []),
        structured=result.get("structured"),
        assets=list(result.get("assets") or []),
        teaching=dict(result["teaching"]),
        guided_mode=result.get("guided_mode"),
        media_kind=result.get("media_kind"),
    )
