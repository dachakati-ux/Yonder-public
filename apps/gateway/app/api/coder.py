"""Gateway-only coder APIs — workspaces, tools, staged runs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from yonder_contracts.errors import ErrorCode

from app.context import get_principal, get_request_id
from app.errors import error_response
from app.services.coder_workspace import CoderError

router = APIRouter(prefix="/v1/coder", tags=["coder"])


class CreateCageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = ""


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    label: str = ""


class CloneBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    label: str = ""
    ref: str | None = None


class ListDirBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    path: str = "."


class ReadFileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    path: str = "."


class ApplyPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    patch: str = Field(min_length=1)


class RunCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    command: str = Field(min_length=1)


class CreateRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    check_command: str = "python -m pytest"


class AdvanceRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Kept for API compatibility; the executor always drives runs to a
    # terminal/blocked state in the background.
    auto: bool = False
    # wait=true blocks the request until the run reaches a resting state
    # (useful for scripts and tests; long runs should poll instead).
    wait: bool = False


def _coder(request: Request):
    return getattr(request.app.state, "coder", None)


def _runs(request: Request):
    return getattr(request.app.state, "coder_runs", None)


def _require_coder(principal: Any, request_id: str):
    if principal.has_capability("coder.use") or principal.has_capability("lab.use"):
        return None
    return error_response(
        ErrorCode.PERMISSION_CONTEXT_REQUIRED,
        "Principal lacks coder.use capability.",
        request_id,
    )


def _map_error(exc: CoderError, request_id: str):
    status = 400
    if exc.code in {"workspace_not_found", "path_not_found", "run_not_found", "source_not_found"}:
        status = 404
    if exc.code in {
        "path_escape",
        "secret_path_denied",
        "path_not_grantable",
        "path_not_readable",
        "write_locked",
        "command_not_allowlisted",
        "check_args_denied",
        "push_refused_without_dual_approve",
        "remote_not_allowed",
    }:
        status = 403
    if exc.code == "llm_unavailable":
        status = 503
    return error_response(
        ErrorCode.INVALID_REQUEST,
        exc.message,
        request_id,
        status_code=status,
        details={"code": exc.code, **exc.details},
    )


def _service_or_501(request: Request, request_id: str):
    service = _coder(request)
    if service is None:
        return None, error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Coder service is not wired on this Gateway.",
            request_id,
            status_code=501,
        )
    return service, None


@router.get("/status")
async def coder_status(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service = _coder(request)
    if service is None:
        return {
            "request_id": request_id,
            "enabled": False,
            "phase": "disabled",
            "availability": "not_supported",
            "write_unlock": False,
            "message": "Coder service is not wired on this Gateway.",
        }
    body = service.status()
    if _runs(request) is not None:
        # Pipeline orchestrator is wired — surface g_pipeline when write unlocked.
        if service.write_unlock:
            body["phase"] = "g_pipeline"
        elif body["phase"] in {"a_assistant", "b_sandbox_read", "c_human_path"}:
            body["phase"] = body["phase"]
        body["pipeline"] = True
        body["agent_mode"] = getattr(request.app.state, "coder_agent_mode", "deterministic")
    return {"request_id": request_id, **body}


@router.get("/workspaces")
async def list_workspaces(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    return {
        "request_id": request_id,
        "workspaces": service.list_workspaces(principal.subject),
    }


@router.post("/workspaces")
async def create_cage(request: Request, body: CreateCageBody) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    try:
        workspace = service.create_cage(principal.subject, label=body.label)
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, "workspace": workspace}


@router.post("/workspaces/grant")
async def grant_workspace(request: Request, body: GrantBody) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    try:
        workspace = service.grant_path(principal.subject, body.path, label=body.label)
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, "workspace": workspace}


@router.post("/workspaces/clone")
async def clone_workspace(request: Request, body: CloneBody) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    try:
        workspace = service.clone_local(
            principal.subject, body.source, label=body.label, ref=body.ref
        )
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, "workspace": workspace}


@router.delete("/workspaces/{workspace_id}")
async def revoke_workspace(request: Request, workspace_id: str) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    try:
        service.revoke(principal.subject, workspace_id)
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, "revoked": workspace_id}


@router.post("/tools/list_dir")
async def tool_list_dir(request: Request, body: ListDirBody) -> dict[str, Any]:
    return await _run_tool(request, "list_dir", body)


@router.post("/tools/read_file")
async def tool_read_file(request: Request, body: ReadFileBody) -> dict[str, Any]:
    return await _run_tool(request, "read_file", body)


@router.post("/tools/search")
async def tool_search(request: Request, body: SearchBody) -> dict[str, Any]:
    return await _run_tool(request, "search", body)


@router.post("/tools/apply_patch")
async def tool_apply_patch(request: Request, body: ApplyPatchBody) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    try:
        # Desk apply still requires write_unlock; pipeline stages pass allow_write separately.
        result = service.apply_patch(
            principal.subject, body.workspace_id, body.patch, allow_write=True
        )
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, **result}


@router.post("/tools/run_check")
async def tool_run_check(request: Request, body: RunCheckBody) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    try:
        result = service.run_check(
            principal.subject, body.workspace_id, body.command, allow_write=True
        )
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, **result}


@router.get("/runs")
async def list_runs(request: Request) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    orch = _runs(request)
    if orch is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Coder run orchestrator is not wired.",
            request_id,
            status_code=501,
        )
    return {"request_id": request_id, "runs": orch.list_runs(principal.subject)}


@router.post("/runs")
async def create_run(request: Request, body: CreateRunBody) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    orch = _runs(request)
    if orch is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Coder run orchestrator is not wired.",
            request_id,
            status_code=501,
        )
    try:
        run = orch.create_run(
            principal.subject,
            body.workspace_id,
            body.task,
            check_command=body.check_command,
        )
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, "run": run.to_public()}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    orch = _runs(request)
    if orch is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Coder run orchestrator is not wired.",
            request_id,
            status_code=501,
        )
    try:
        run = orch.get_run(principal.subject, run_id)
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, "run": run.to_public()}


@router.post("/runs/{run_id}/advance")
async def advance_run(request: Request, run_id: str, body: AdvanceRunBody) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    orch = _runs(request)
    executor = getattr(request.app.state, "coder_executor", None)
    if orch is None or executor is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Coder run orchestrator is not wired.",
            request_id,
            status_code=501,
        )
    try:
        submitted = await executor.submit(principal.subject, run_id, wait=body.wait)
        run = orch.get_run(principal.subject, run_id)
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {
        "request_id": request_id,
        "executor": submitted.get("executor"),
        "run": run.to_public(),
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    orch = _runs(request)
    executor = getattr(request.app.state, "coder_executor", None)
    if orch is None or executor is None:
        return error_response(
            ErrorCode.MODEL_UNAVAILABLE,
            "Coder run orchestrator is not wired.",
            request_id,
            status_code=501,
        )
    try:
        result = await executor.cancel(principal.subject, run_id)
        run = orch.get_run(principal.subject, run_id)
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, **result, "run": run.to_public()}


async def _run_tool(request: Request, name: str, body: BaseModel) -> dict[str, Any]:
    principal = get_principal(request)
    request_id = get_request_id(request)
    denied = _require_coder(principal, request_id)
    if denied:
        return denied
    service, err = _service_or_501(request, request_id)
    if err:
        return err
    try:
        if name == "list_dir":
            assert isinstance(body, ListDirBody)
            result = service.list_dir(principal.subject, body.workspace_id, body.path)
        elif name == "read_file":
            assert isinstance(body, ReadFileBody)
            result = service.read_file(principal.subject, body.workspace_id, body.path)
        else:
            assert isinstance(body, SearchBody)
            result = service.search(
                principal.subject, body.workspace_id, body.query, body.path
            )
    except CoderError as exc:
        return _map_error(exc, request_id)
    return {"request_id": request_id, **result}
