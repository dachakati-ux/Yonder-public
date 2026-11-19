"""Adversarial tests for M03.4 tenant, classroom, owner isolation & role-mapping preview."""

from __future__ import annotations

from types import MappingProxyType

from app.api import auth as auth_api
from app.context import RequestContextMiddleware
from app.services.rbac import ResourceScope, authorize_resource_access
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yonder_configuration.models import OidcConfig, SecurityConfig, SecurityMode, YonderSettings
from yonder_contracts.principal import RequestPrincipal


def _principal(
    subject: str,
    *,
    roles: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
    tenant_id: str | None = None,
    class_ids: str = "",
) -> RequestPrincipal:
    attrs: dict[str, str] = {}
    if tenant_id is not None:
        attrs["tenant_id"] = tenant_id
    if class_ids:
        attrs["class_ids"] = class_ids
    return RequestPrincipal(
        subject=subject,
        principal_type="user",
        roles=roles,
        capabilities=capabilities,
        attributes=MappingProxyType(attrs),
    )


def test_student_isolation_and_default_deny() -> None:
    student_a = _principal("student-a", roles=("student",), tenant_id="school-1")
    student_b = _principal("student-b", roles=("student",), tenant_id="school-1")
    unmapped = _principal("unknown-user", roles=(), tenant_id="school-1")

    own_doc = ResourceScope(tenant_id="school-1", class_id="cs-101", owner_subject="student-a")
    assert authorize_resource_access(student_a, own_doc, action="read").allowed is True
    assert authorize_resource_access(student_a, own_doc, action="write").allowed is True
    assert authorize_resource_access(student_b, own_doc, action="read").allowed is False
    assert authorize_resource_access(unmapped, own_doc, action="read").allowed is False


def test_teacher_classroom_and_tenant_boundaries() -> None:
    teacher = _principal(
        "teacher-1",
        roles=("teacher",),
        tenant_id="school-1",
        class_ids="cs-101,cs-102",
    )
    in_class = ResourceScope(tenant_id="school-1", class_id="cs-101", owner_subject="student-a")
    other_class = ResourceScope(tenant_id="school-1", class_id="cs-999", owner_subject="student-a")
    other_tenant = ResourceScope(tenant_id="school-2", class_id="cs-101", owner_subject="student-a")

    assert authorize_resource_access(teacher, in_class, action="read").allowed is True
    assert authorize_resource_access(teacher, other_class, action="read").allowed is False
    assert authorize_resource_access(teacher, other_tenant, action="read").allowed is False


def test_admin_cannot_cross_tenant() -> None:
    admin_s1 = _principal(
        "admin-1",
        roles=("it_admin",),
        capabilities=("operations.manage",),
        tenant_id="school-1",
    )
    res_s1 = ResourceScope(tenant_id="school-1", owner_subject="student-a")
    res_s2 = ResourceScope(tenant_id="school-2", owner_subject="student-b")

    assert authorize_resource_access(admin_s1, res_s1, action="admin").allowed is True
    assert authorize_resource_access(admin_s1, res_s2, action="admin").allowed is False


def test_role_mapping_preview_requires_operations_manage() -> None:
    class _StaticProvider:
        def __init__(self, principal: RequestPrincipal) -> None:
            self._p = principal

        async def resolve(self, _request) -> RequestPrincipal:
            return self._p

    oidc_cfg = OidcConfig(role_mappings={"teachers": ("teacher",)})
    settings = YonderSettings(security=SecurityConfig(mode=SecurityMode.DEVELOPMENT, oidc=oidc_cfg))

    student_app = FastAPI()
    student_app.state.settings = settings
    student_app.add_middleware(
        RequestContextMiddleware,
        provider=_StaticProvider(_principal("student-1", roles=("student",))),
        service_name="test",
    )
    student_app.include_router(auth_api.router)
    assert (
        TestClient(student_app).post("/v1/auth/preview-mapping", json={"groups": ["teachers"]})
        .status_code
        == 403
    )

    admin_app = FastAPI()
    admin_app.state.settings = settings
    admin_app.add_middleware(
        RequestContextMiddleware,
        provider=_StaticProvider(
            _principal("admin-1", roles=("it_admin",), capabilities=("operations.manage",))
        ),
        service_name="test",
    )
    admin_app.include_router(auth_api.router)
    res = TestClient(admin_app).post(
        "/v1/auth/preview-mapping", json={"groups": ["teachers", "unknown"]}
    )
    assert res.status_code == 200
    assert res.json()["roles"] == ["teacher"]
    assert "chat.use" in res.json()["capabilities"]
