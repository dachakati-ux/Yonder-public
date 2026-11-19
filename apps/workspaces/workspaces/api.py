"""Authenticated Workspaces metadata API (M05.1/M05.2, execution disabled)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import WorkspacePrincipal, can_access_project, is_tenant_admin, make_auth_dependency
from .store import QuotaExceededError, WorkspaceStore, WorkspaceStoreError


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=64)
    class_id: str | None = Field(default=None, max_length=64)
    quota_mb: int = Field(default=512, ge=1, le=10240)
    retention_days: int = Field(default=90, ge=1, le=3650)


def create_app(
    *,
    database_path: Path | None = None,
    service_token: str | None = None,
    max_projects_per_user: int = 10,
    max_quota_mb_per_project: int = 2048,
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
            "execution_enabled": False,
            "vm_isolation": "not_implemented",
            "proxmox_adapter": "not_implemented",
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

    return app
