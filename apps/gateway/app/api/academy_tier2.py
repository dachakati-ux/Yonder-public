"""Academy Tier 2 APIs — cohorts, instructor, workbench, capacity."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import raise_error
from app.services.capacity import capacity_snapshot
from app.services.cohorts import (
    ChallengeSubmission,
    catalog_public,
    evaluate_challenge,
    get_cohort_catalog,
)
from app.services.curriculum import get_curriculum
from app.services.instructor import aggregate_track_completion


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")

router = APIRouter(prefix="/v1/lab", tags=["lab-tier2"])


def _require_lab(principal, request_id: str) -> None:
    if not principal.has_capability("lab.use"):
        raise_error(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks lab.use capability.",
            request_id,
        )


def _require_instruct(principal, request_id: str) -> None:
    if not (
        principal.has_capability("academy.instruct")
        or principal.has_capability("operations.manage")
    ):
        raise_error(
            ErrorCode.PERMISSION_CONTEXT_REQUIRED,
            "Principal lacks academy.instruct (or operations.manage) capability.",
            request_id,
        )


class ChallengeSubmitRequest(BaseModel):
    model_config = {"extra": "forbid"}

    answer: str = Field(min_length=1, max_length=12000)


class InstructorResetRequest(BaseModel):
    model_config = {"extra": "forbid"}

    principal_subject: str = Field(min_length=1, max_length=256)


class QuarantineRequest(BaseModel):
    model_config = {"extra": "forbid"}

    corpus_id: str = Field(min_length=1, max_length=128)
    action: Literal["quarantine", "release"] = "quarantine"


class WorkbenchNoteRequest(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="manual", max_length=64)


class WorkbenchNoteUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    tags: list[str] | None = None
    source: str | None = Field(default=None, max_length=64)


@router.get("/cohorts")
async def list_cohorts(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    catalog = get_cohort_catalog()
    body = catalog_public(catalog)
    submissions = {
        s.challenge_id: s.model_dump()
        for s in request.app.state.challenges.list_for(principal.subject)
    }
    for cohort in body["cohorts"]:
        for challenge in cohort["challenges"]:
            challenge["submission"] = submissions.get(challenge["id"])
    return body


@router.post("/cohorts/challenges/{challenge_id}/submit")
async def submit_challenge(
    challenge_id: str, body: ChallengeSubmitRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    catalog = get_cohort_catalog()
    challenge = next((c for c in catalog.challenges if c.id == challenge_id), None)
    if challenge is None:
        raise_error(ErrorCode.INVALID_REQUEST, f"Unknown challenge: {challenge_id}", request_id)
    passed, met, missed, explanation = evaluate_challenge(challenge, body.answer)
    submission = ChallengeSubmission(
        challenge_id=challenge_id,
        principal_subject=principal.subject,
        answer=body.answer,
        criteria_met=met,
        criteria_missed=missed,
        passed=passed,
        submitted_at=_now(),
    )
    saved = request.app.state.challenges.submit(submission)
    return {
        "passed": saved.passed,
        "criteria_met": saved.criteria_met,
        "criteria_missed": saved.criteria_missed,
        "explanation": explanation,
        "submission": saved.model_dump(),
        "note": "Completion is criteria met/missed — not a numeric vanity score.",
    }


@router.get("/cohorts/challenges/{challenge_id}/board")
async def challenge_board(challenge_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    catalog = get_cohort_catalog()
    if not any(c.id == challenge_id for c in catalog.challenges):
        raise_error(ErrorCode.INVALID_REQUEST, f"Unknown challenge: {challenge_id}", request_id)
    return {
        "challenge_id": challenge_id,
        "completions": request.app.state.challenges.leaderboard(challenge_id),
        "note": "Board lists principals who met criteria. No invented ranks or scores.",
    }


@router.get("/instructor/summary")
async def instructor_summary(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_instruct(principal, request_id)
    curriculum = get_curriculum()
    all_progress = await request.app.state.progress.list_all_progress()
    summary = aggregate_track_completion(curriculum=curriculum, all_progress=all_progress)
    summary["quarantined_corpora"] = request.app.state.corpus_quarantine.list()
    summary["challenge_completions"] = {
        c.id: len(request.app.state.challenges.leaderboard(c.id))
        for c in get_cohort_catalog().challenges
    }
    return summary


@router.post("/instructor/progress/reset")
async def instructor_reset_progress(
    body: InstructorResetRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_instruct(principal, request_id)
    cleared = await request.app.state.progress.reset_progress(body.principal_subject)
    return {
        "principal_subject": body.principal_subject,
        "cleared": cleared,
        "reset_by": principal.subject,
    }


@router.post("/instructor/corpus")
async def instructor_corpus(body: QuarantineRequest, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_instruct(principal, request_id)
    if body.action == "quarantine":
        request.app.state.corpus_quarantine.quarantine(body.corpus_id)
    else:
        request.app.state.corpus_quarantine.release(body.corpus_id)
    return {
        "corpus_id": body.corpus_id,
        "action": body.action,
        "quarantined": request.app.state.corpus_quarantine.list(),
        "by": principal.subject,
    }


@router.get("/workbench")
async def workbench_list(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    notes = request.app.state.workbench.list_notes(principal.subject)
    return {"notes": [n.model_dump() for n in notes]}


@router.post("/workbench/notes")
async def workbench_create(body: WorkbenchNoteRequest, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    try:
        note = request.app.state.workbench.create(
            principal_subject=principal.subject,
            title=body.title,
            body=body.body,
            tags=body.tags,
            source=body.source,
        )
    except ValueError as exc:
        raise_error(ErrorCode.INVALID_REQUEST, str(exc), request_id)
    return {"note": note.model_dump()}


@router.patch("/workbench/notes/{note_id}")
async def workbench_update(
    note_id: str, body: WorkbenchNoteUpdateRequest, request: Request
) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    note = request.app.state.workbench.update(
        note_id,
        principal.subject,
        title=body.title,
        body=body.body,
        tags=body.tags,
        source=body.source,
    )
    if note is None:
        raise_error(ErrorCode.INVALID_REQUEST, "Note not found.", request_id)
    return {"note": note.model_dump()}


@router.delete("/workbench/notes/{note_id}")
async def workbench_delete(note_id: str, request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    ok = request.app.state.workbench.delete(note_id, principal.subject)
    if not ok:
        raise_error(ErrorCode.INVALID_REQUEST, "Note not found.", request_id)
    return {"deleted": note_id}


@router.get("/workbench/export")
async def workbench_export(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    return request.app.state.workbench.export(principal.subject)


@router.get("/capacity")
async def lab_capacity(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    _require_lab(principal, request_id)
    return capacity_snapshot(request)
