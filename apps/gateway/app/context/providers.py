"""Gateway request-context providers."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any

from fastapi import HTTPException, Request
from yonder_configuration.models import DevelopmentPrincipalConfig, SecurityConfig, SecurityMode
from yonder_configuration.worker_auth import worker_token_matches
from yonder_contracts.errors import ErrorBody, ErrorCode, ErrorResponse
from yonder_contracts.principal import RequestPrincipal


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


def build_request_context_provider(security: SecurityConfig) -> Any:
    if security.mode == SecurityMode.DEVELOPMENT:
        return DevelopmentRequestContextProvider(security.development_principal)
    if security.mode == SecurityMode.TRUSTED_INGRESS:
        return TrustedIngressRequestContextProvider(security)
    raise ValueError(f"Unsupported security mode: {security.mode}")
