"""Authenticated Workspaces metadata API (M05.1/M05.2, execution disabled)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import WorkspacePrincipal, can_access_project, is_tenant_admin, make_auth_dependency
from .provisioner import ProvisionerError, SessionSpec, WorkspaceProvisioner
from .store import QuotaExceededError, WorkspaceStore, WorkspaceStoreError


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=64)
    class_id: str | None = Field(default=None, max_length=64)
    quota_mb: int = Field(default=512, ge=1, le=10240)
    retention_days: int = Field(default=90, ge=1, le=3650)


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    template_digest: str = Field(min_length=10, max_length=160)
    cpu_cores: int = Field(default=1, ge=1, le=4)
    memory_mb: int = Field(default=1024, ge=256, le=8192)
    ttl_seconds: int = Field(default=1800, ge=60, le=14400)
    network_profile: Literal["none", "isolated_vlan"] = "none"


def create_app(
    *,
    database_path: Path | None = None,
    service_token: str | None = None,
    max_projects_per_user: int = 10,
    max_quota_mb_per_project: int = 2048,
    max_active_sessions_per_user: int = 2,
    provisioner: WorkspaceProvisioner | None = None,
) -> FastAPI:
    token = (
        service_token
        if service_token is not None
        else os.getenv("YONDER_WORKSPACES_SERVICE_TOKEN", "")
    )
    authenticate = make_auth_dependency(token)
    auth_dep = Depends(authenticate)
    store = WorkspaceStore(
        database_path or Path(os.getenv("YONDER_WORKSPACES_DB", "/var/lib/yonder/workspaces.db")),
        max_projects_per_user=max_projects_per_user,
        max_quota_mb_per_project=max_quota_mb_per_project,
        max_active_sessions_per_user=max_active_sessions_per_user,
    )
    app = FastAPI(
        title="Yonder Workspaces",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "yonder-workspaces"}

    @app.get("/workspaces/v1/capabilities")
    async def capabilities(
        _principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        return {
            "service": "yonder-workspaces",
            "metadata_enabled": True,
            "execution_enabled": provisioner is not None,
            "vm_isolation": provisioner.backend_name if provisioner else "not_implemented",
            "proxmox_adapter": (
                "configured"
                if provisioner and provisioner.backend_name == "proxmox_vm"
                else "not_implemented"
            ),
        }

    @app.get("/workspaces/v1/projects")
    async def list_projects(
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        items = store.list_projects_for_principal(
            owner_subject=principal.subject,
            tenant_id=principal.tenant_id,
            class_ids=principal.class_ids,
            is_teacher="teacher" in principal.roles,
        )
        return {"projects": items}

    @app.post("/workspaces/v1/projects", status_code=201)
    async def create_project(
        body: CreateProjectRequest,
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        try:
            project = store.create_project(
                name=body.name,
                owner_subject=principal.subject,
                tenant_id=principal.tenant_id,
                class_id=body.class_id,
                quota_mb=body.quota_mb,
                retention_days=body.retention_days,
            )
        except QuotaExceededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkspaceStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"project": project}

    @app.get("/workspaces/v1/projects/{project_id}")
    async def get_project(
        project_id: str,
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if not can_access_project(
            principal,
            owner_subject=project["owner_subject"],
            tenant_id=project["tenant_id"],
            class_id=project["class_id"],
            action="read",
        ):
            store.record_audit(
                project_id=project_id,
                actor_subject=principal.subject,
                tenant_id=principal.tenant_id,
                action="access_denied",
                detail="read",
            )
            raise HTTPException(status_code=403, detail="Access denied")
        return {"project": project}

    @app.get("/workspaces/v1/projects/{project_id}/export")
    async def export_project(
        project_id: str,
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if not can_access_project(
            principal,
            owner_subject=project["owner_subject"],
            tenant_id=project["tenant_id"],
            class_id=project["class_id"],
            action="export",
        ):
            store.record_audit(
                project_id=project_id,
                actor_subject=principal.subject,
                tenant_id=principal.tenant_id,
                action="access_denied",
                detail="export",
            )
            raise HTTPException(status_code=403, detail="Access denied")
        store.record_audit(
            project_id=project_id,
            actor_subject=principal.subject,
            tenant_id=principal.tenant_id,
            action="project_exported",
            detail="metadata_export",
        )
        return {"export_version": 1, "project": project}

    @app.delete("/workspaces/v1/projects/{project_id}")
    async def delete_project(
        project_id: str,
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if not can_access_project(
            principal,
            owner_subject=project["owner_subject"],
            tenant_id=project["tenant_id"],
            class_id=project["class_id"],
            action="delete",
        ):
            store.record_audit(
                project_id=project_id,
                actor_subject=principal.subject,
                tenant_id=principal.tenant_id,
                action="access_denied",
                detail="delete",
            )
            raise HTTPException(status_code=403, detail="Access denied")
        store.delete_project(project_id, actor_subject=principal.subject)
        return {"deleted": True, "project_id": project_id}

    @app.get("/workspaces/v1/admin/policies")
    async def admin_policies(
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        if not is_tenant_admin(principal):
            raise HTTPException(status_code=403, detail="Admin role required")
        return {
            "tenant_id": principal.tenant_id,
            "max_projects_per_user": store.max_projects_per_user,
            "max_quota_mb_per_project": store.max_quota_mb_per_project,
            "execution_enabled": False,
        }

    @app.get("/workspaces/v1/admin/audit")
    async def admin_audit(
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        if not is_tenant_admin(principal):
            raise HTTPException(status_code=403, detail="Admin role required")
        return {"events": store.list_audit(tenant_id=principal.tenant_id)}

    @app.get("/workspaces/v1/projects/{project_id}/sessions")
    async def list_sessions(
        project_id: str,
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if not can_access_project(
            principal,
            owner_subject=project["owner_subject"],
            tenant_id=project["tenant_id"],
            class_id=project["class_id"],
            action="read",
        ):
            raise HTTPException(status_code=403, detail="Access denied")
        return {"sessions": store.list_sessions_for_project(project_id)}

    @app.post("/workspaces/v1/projects/{project_id}/sessions", status_code=201)
    async def create_session(
        project_id: str,
        body: CreateSessionRequest,
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        if provisioner is None:
            raise HTTPException(
                status_code=501,
                detail="VM/microVM execution backend is not configured",
            )
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if not can_access_project(
            principal,
            owner_subject=project["owner_subject"],
            tenant_id=project["tenant_id"],
            class_id=project["class_id"],
            action="write",
        ):
            store.record_audit(
                project_id=project_id,
                actor_subject=principal.subject,
                tenant_id=principal.tenant_id,
                action="access_denied",
                detail="create_session",
            )
            raise HTTPException(status_code=403, detail="Access denied")
        try:
            session, created = store.reserve_session(
                project_id=project_id,
                owner_subject=principal.subject,
                tenant_id=principal.tenant_id,
                idempotency_key=body.idempotency_key,
                template_digest=body.template_digest,
                cpu_cores=body.cpu_cores,
                memory_mb=body.memory_mb,
                ttl_seconds=body.ttl_seconds,
                backend=provisioner.backend_name,
            )
        except QuotaExceededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not created:
            return {"session": session, "idempotent_replay": True}
        spec = SessionSpec(
            session_id=session["session_id"],
            project_id=project_id,
            owner_subject=principal.subject,
            tenant_id=principal.tenant_id,
            template_digest=body.template_digest,
            cpu_cores=body.cpu_cores,
            memory_mb=body.memory_mb,
            ttl_seconds=body.ttl_seconds,
            network_profile=body.network_profile,
        )
        try:
            backend_ref = await provisioner.provision_session(spec)
        except ProvisionerError as exc:
            store.mark_session_stopped(session["session_id"], status="failed")
            store.record_audit(
                project_id=project_id,
                actor_subject=principal.subject,
                tenant_id=principal.tenant_id,
                action="session_failed",
                detail=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        running = store.mark_session_running(session["session_id"], backend_ref)
        store.record_audit(
            project_id=project_id,
            actor_subject=principal.subject,
            tenant_id=principal.tenant_id,
            action="session_created",
            detail=backend_ref,
        )
        return {"session": running, "idempotent_replay": False}

    @app.post("/workspaces/v1/sessions/{session_id}/stop")
    async def stop_session(
        session_id: str,
        principal: WorkspacePrincipal = auth_dep,
    ) -> dict[str, Any]:
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        project = store.get_project(session["project_id"])
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if not can_access_project(
            principal,
            owner_subject=project["owner_subject"],
            tenant_id=project["tenant_id"],
            class_id=project["class_id"],
            action="write",
        ) and not is_tenant_admin(principal):
            raise HTTPException(status_code=403, detail="Access denied")
        if provisioner is not None and session.get("backend_ref"):
            await provisioner.terminate_session(session_id, str(session["backend_ref"]))
        stopped = store.mark_session_stopped(session_id, status="stopped")
        store.record_audit(
            project_id=session["project_id"],
            actor_subject=principal.subject,
            tenant_id=principal.tenant_id,
            action="session_stopped",
            detail=session_id,
        )
        return {"session": stopped}

    return app
