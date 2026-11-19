"""Tenant, classroom, and owner-scoped RBAC policy enforcement (M03.4, default-deny)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from yonder_contracts.principal import RequestPrincipal


@dataclass(frozen=True)
class ResourceScope:
    tenant_id: str | None = None
    class_id: str | None = None
    owner_subject: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


def _principal_class_ids(principal: RequestPrincipal) -> set[str]:
    raw = principal.attributes.get("class_ids", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def authorize_resource_access(
    principal: RequestPrincipal,
    resource: ResourceScope,
    *,
    action: Literal["read", "write", "admin"] = "read",
) -> PolicyDecision:
    """Enforce default-deny tenant, classroom, and owner isolation."""
    principal_tenant = (principal.attributes.get("tenant_id") or "").strip() or None

    # Tenant isolation: when both sides carry a tenant_id, they must match.
    if principal_tenant is not None and resource.tenant_id is not None:
        if principal_tenant != resource.tenant_id:
            return PolicyDecision(False, "Cross-tenant access denied.")
    if principal_tenant is not None and resource.tenant_id is None and action == "admin":
        return PolicyDecision(False, "Tenant-scoped principal cannot administer unscoped resource.")

    roles = set(principal.roles)

    # Institutional admins within their tenant scope.
    if (
        ("it_admin" in roles or "security_admin" in roles)
        and principal.has_capability("operations.manage")
    ):
        return PolicyDecision(True, "Permitted by tenant-scoped admin role.")

    # Owner access (e.g., student or teacher accessing their own workspace/chat).
    if resource.owner_subject and principal.subject == resource.owner_subject:
        if action in {"read", "write"}:
            return PolicyDecision(True, "Permitted for resource owner.")

    # Teacher classroom scope: only classes explicitly assigned to the teacher.
    if "teacher" in roles and resource.class_id:
        if resource.class_id in _principal_class_ids(principal):
            return PolicyDecision(True, "Permitted for assigned classroom.")
        return PolicyDecision(False, "Cross-classroom access denied.")

    return PolicyDecision(False, "Denied by default-deny resource policy.")
