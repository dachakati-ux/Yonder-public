"""Verified service-to-service + principal identity extraction and default-deny RBAC."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, status


@dataclass(frozen=True)
class WorkspacePrincipal:
    subject: str
    roles: tuple[str, ...]
    tenant_id: str | None
    class_ids: tuple[str, ...]


def make_auth_dependency(service_token: str):
    if len(service_token) < 32 or service_token.strip() != service_token:
        raise RuntimeError("Workspaces service requires a token of at least 32 characters")

    def authenticate(
        authorization: str | None = Header(default=None),
        x_yonder_subject: str | None = Header(default=None),
        x_yonder_roles: str | None = Header(default=None),
        x_yonder_tenant_id: str | None = Header(default=None),
        x_yonder_class_ids: str | None = Header(default=None),
    ) -> WorkspacePrincipal:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if (
            not authorization
            or not authorization.startswith("Bearer ")
            or not secrets.compare_digest(supplied, service_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Workspaces service authentication required",
            )
        subject = (x_yonder_subject or "").strip()
        if not subject:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Verified user subject is required",
            )
        roles = tuple(r.strip() for r in (x_yonder_roles or "").split(",") if r.strip())
        tenant_id = (x_yonder_tenant_id or "").strip() or None
        class_ids = tuple(c.strip() for c in (x_yonder_class_ids or "").split(",") if c.strip())
        return WorkspacePrincipal(
            subject=subject,
            roles=roles,
            tenant_id=tenant_id,
            class_ids=class_ids,
        )

    return authenticate


def can_access_project(
    principal: WorkspacePrincipal,
    *,
    owner_subject: str,
    tenant_id: str | None,
    class_id: str | None,
    action: str,
) -> bool:
    """Default-deny tenant, classroom, and owner isolation."""
    if principal.tenant_id is not None and tenant_id is not None:
        if principal.tenant_id != tenant_id:
            return False
    if principal.tenant_id is not None and tenant_id is None:
        return False

    if principal.subject == owner_subject and action in {"read", "write", "export", "delete"}:
        return True

    if "teacher" in principal.roles and class_id and class_id in principal.class_ids:
        if action in {"read", "export"}:
            return True

    return False


def is_tenant_admin(principal: WorkspacePrincipal) -> bool:
    return bool({"it_admin", "security_admin"} & set(principal.roles))
