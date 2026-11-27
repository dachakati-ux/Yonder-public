"""Gateway request-context providers."""

from __future__ import annotations

import ipaddress
import json
import secrets
from types import MappingProxyType
from typing import Any

from fastapi import HTTPException, Request
from yonder_configuration.models import DevelopmentPrincipalConfig, SecurityConfig, SecurityMode
from yonder_configuration.worker_auth import worker_token_matches
from yonder_contracts.errors import ErrorBody, ErrorCode, ErrorResponse
from yonder_contracts.principal import RequestPrincipal

from app.services.oidc import (
    OidcError,
    SessionRevocationStore,
    has_mfa_assurance,
    verify_session_payload,
)


def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _parse_attributes(raw: str | None) -> MappingProxyType[str, str]:
    """Accept JSON object or key=value pairs separated by commas."""

    if not raw or not raw.strip():
        return MappingProxyType({})
    text = raw.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message="Trusted attributes header was not valid JSON.",
                        request_id="unknown",
                    )
                ).model_dump(),
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message="Trusted attributes header must be a JSON object.",
                        request_id="unknown",
                    )
                ).model_dump(),
            )
        return MappingProxyType({str(key): str(value) for key, value in parsed.items()})

    attrs: dict[str, str] = {}
    for part in text.split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            attrs[key] = value.strip()
    return MappingProxyType(attrs)


class DevelopmentRequestContextProvider:
    """Injects a configured mock principal for local development only."""

    def __init__(self, principal_config: DevelopmentPrincipalConfig) -> None:
        self._principal_config = principal_config

    async def resolve(self, request: Request) -> RequestPrincipal:
        upstream = request.headers.get("x-request-id")
        return RequestPrincipal(
            subject=self._principal_config.subject,
            principal_type=self._principal_config.principal_type,
            roles=tuple(self._principal_config.roles),
            groups=tuple(self._principal_config.groups),
            capabilities=tuple(self._principal_config.capabilities),
            attributes=MappingProxyType(dict(self._principal_config.attributes)),
            upstream_request_id=upstream,
            authentication_context_id="development-mock",
        )


class TrustedIngressRequestContextProvider:
    """Normalizes trusted ingress headers into RequestPrincipal.

    This does not authenticate. It fails closed when required trusted
    context is missing. Header names are configuration-driven so corporate
    integration can be wired without guessing upstream IAM details.
    """

    def __init__(self, security: SecurityConfig) -> None:
        self._security = security
        self._proxy_networks = tuple(
            ipaddress.ip_network(cidr, strict=False)
            for cidr in security.trusted_proxy_cidrs
        )

    def _verify_ingress_authenticity(self, request: Request, request_id: str) -> None:
        if self._proxy_networks:
            client_host = request.client.host if request.client else None
            allowed = False
            if client_host:
                try:
                    addr = ipaddress.ip_address(client_host)
                    allowed = any(addr in net for net in self._proxy_networks)
                except ValueError:
                    allowed = False
            if not allowed:
                raise HTTPException(
                    status_code=401,
                    detail=ErrorResponse(
                        error=ErrorBody(
                            code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                            message="Untrusted ingress peer rejected.",
                            request_id=request_id,
                        )
                    ).model_dump(),
                )
        expected_secret = (self._security.trusted_ingress_shared_secret or "").strip()
        if expected_secret:
            supplied = request.headers.getlist(self._security.trusted_ingress_secret_header)
            if len(supplied) != 1 or not secrets.compare_digest(
                supplied[0].encode("utf-8"), expected_secret.encode("utf-8")
            ):
                raise HTTPException(
                    status_code=401,
                    detail=ErrorResponse(
                        error=ErrorBody(
                            code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                            message="Untrusted ingress headers rejected.",
                            request_id=request_id,
                        )
                    ).model_dump(),
                )

    async def resolve(self, request: Request) -> RequestPrincipal:
        subject = request.headers.get(self._security.trusted_subject_header)
        principal_type = request.headers.get(
            self._security.trusted_principal_type_header, "user"
        )
        request_id = request.headers.get(
            self._security.trusted_request_id_header,
            request.headers.get("x-request-id", "unknown"),
        )
        # Worker agents authenticate with the shared service token instead of
        # trusted ingress headers (they call the gateway directly, not through
        # corporate ingress). The synthesized principal is narrowly scoped to
        # workers.report — it grants no operator or admin capability.
        if not subject or not subject.strip():
            worker_token = request.headers.get(self._security.worker_token_header)
            if worker_token and worker_token_matches(self._security, worker_token):
                return RequestPrincipal(
                    subject="yonder-worker-agent",
                    principal_type="service",
                    capabilities=("workers.report",),
                    authentication_context_id="worker-shared-secret",
                )
        if not subject or not subject.strip():
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message=(
                            "Trusted request context was not supplied by the "
                            "upstream security layer."
                        ),
                        request_id=request_id,
                    )
                ).model_dump(),
            )

        self._verify_ingress_authenticity(request, request_id)

        capabilities = _split_csv(
            request.headers.get(self._security.trusted_capabilities_header)
        )
        roles = _split_csv(request.headers.get(self._security.trusted_roles_header))
        groups = _split_csv(request.headers.get(self._security.trusted_groups_header))
        try:
            attributes = _parse_attributes(
                request.headers.get(self._security.trusted_attributes_header)
            )
        except HTTPException as exc:
            # Attach the resolved request id when attributes parsing fails.
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                exc.detail["error"]["request_id"] = request_id
            raise

        return RequestPrincipal(
            subject=subject.strip(),
            principal_type=(principal_type or "user").strip() or "user",
            roles=roles,
            groups=groups,
            capabilities=capabilities,
            attributes=attributes,
            upstream_request_id=request.headers.get(
                self._security.trusted_request_id_header
            ),
            authentication_context_id="trusted-ingress",
        )


class OidcSessionRequestContextProvider:
    """Resolves RequestPrincipal from an HMAC-signed OIDC session cookie."""

    MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(
        self,
        security: SecurityConfig,
        *,
        revocation_store: SessionRevocationStore | None = None,
    ) -> None:
        self._security = security
        self._revocation_store = revocation_store

    async def resolve(self, request: Request) -> RequestPrincipal:
        request_id = request.headers.get("x-request-id", "unknown")
        cookie_token = request.cookies.get(self._security.oidc.session_cookie_name)
        if not cookie_token:
            worker_token = request.headers.get(self._security.worker_token_header)
            if worker_token and worker_token_matches(self._security, worker_token):
                return RequestPrincipal(
                    subject="yonder-worker-agent",
                    principal_type="service",
                    capabilities=("workers.report",),
                    authentication_context_id="worker-shared-secret",
                )
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message="Authentication required.",
                        request_id=request_id,
                    )
                ).model_dump(),
            )

        secret = (self._security.oidc.session_secret or "").strip()
        try:
            payload = verify_session_payload(cookie_token, secret)
        except OidcError as exc:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message=str(exc),
                        request_id=request_id,
                    )
                ).model_dump(),
            ) from exc

        jti = str(payload.get("jti", "")).strip()
        rev_store = self._revocation_store or getattr(
            request.app.state, "revoked_sessions", None
        )
        if jti and rev_store is not None and rev_store.is_revoked(jti):
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message="Session has been revoked.",
                        request_id=request_id,
                    )
                ).model_dump(),
            )

        if request.method.upper() in self.MUTATING_METHODS:
            expected_csrf = str(payload.get("csrf", ""))
            supplied_csrf = request.headers.get("x-yonder-csrf-token") or ""
            if not expected_csrf or not secrets.compare_digest(supplied_csrf, expected_csrf):
                raise HTTPException(
                    status_code=403,
                    detail=ErrorResponse(
                        error=ErrorBody(
                            code=ErrorCode.PERMISSION_CONTEXT_REQUIRED,
                            message="Missing or invalid CSRF token.",
                            request_id=request_id,
                        )
                    ).model_dump(),
                )

        subject = str(payload.get("sub", "")).strip()
        if not subject:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorBody(
                        code=ErrorCode.SECURITY_CONTEXT_UNAVAILABLE,
                        message="Session missing subject.",
                        request_id=request_id,
                    )
                ).model_dump(),
            )

        amr = [str(a) for a in payload.get("amr", ()) if str(a)]
        acr = str(payload.get("acr") or "").strip()
        mfa_ok = has_mfa_assurance(amr, acr, self._security.oidc)

        return RequestPrincipal(
            subject=subject,
            principal_type="user",
            roles=tuple(str(r) for r in payload.get("roles", ()) if str(r)),
            groups=tuple(str(g) for g in payload.get("groups", ()) if str(g)),
            capabilities=tuple(str(c) for c in payload.get("capabilities", ()) if str(c)),
            attributes=MappingProxyType(
                {
                    "csrf_token": str(payload.get("csrf", "")),
                    "jti": jti,
                    "amr": ",".join(amr),
                    "acr": acr,
                    "mfa_verified": "true" if mfa_ok else "false",
                }
            ),
            upstream_request_id=request.headers.get("x-request-id"),
            authentication_context_id=f"oidc:{self._security.oidc.provider_template}",
        )


def build_request_context_provider(
    security: SecurityConfig,
    *,
    revocation_store: SessionRevocationStore | None = None,
) -> Any:
    if security.mode == SecurityMode.DEVELOPMENT:
        return DevelopmentRequestContextProvider(security.development_principal)
    if security.mode == SecurityMode.TRUSTED_INGRESS:
        return TrustedIngressRequestContextProvider(security)
    if security.mode == SecurityMode.OIDC:
        return OidcSessionRequestContextProvider(security, revocation_store=revocation_store)
    raise ValueError(f"Unsupported security mode: {security.mode}")
