"""Optional, replaceable Convyx agent-collaboration adapter (M07)."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from yonder_contracts.principal import RequestPrincipal

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")


class ConvyxError(ValueError):
    """Raised when Convyx adapter configuration or remote call fails."""


@dataclass(frozen=True)
class ConvyxConfig:
    enabled: bool = False
    base_url: str = ""
    service_token: str | None = None
    timeout_seconds: float = 10.0
    allow_insecure_http_in_dev: bool = False

    @classmethod
    def from_env(cls) -> ConvyxConfig:
        enabled = os.getenv("YONDER_CONVYX_ENABLED", "").lower() in {"1", "true", "yes"}
        base_url = os.getenv("YONDER_CONVYX_URL", "").strip()
        token = os.getenv("YONDER_CONVYX_TOKEN", "").strip() or None
        allow_http = os.getenv("YONDER_CONVYX_ALLOW_HTTP_IN_DEV", "").lower() in {
            "1",
            "true",
            "yes",
        }
        return cls(
            enabled=enabled,
            base_url=base_url,
            service_token=token,
            allow_insecure_http_in_dev=allow_http,
        )


def validate_convyx_url(url: str, *, allow_insecure_http: bool = False) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        raise ConvyxError("Convyx base_url is empty.")
    parsed = urlsplit(cleaned)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ConvyxError("Convyx URL must not contain userinfo or fragments.")
    allowed_schemes = {"https", "http"} if allow_insecure_http else {"https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise ConvyxError("Convyx URL must use HTTPS and include a valid hostname.")
    if not allow_insecure_http:
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"}:
            raise ConvyxError("Loopback Convyx URL requires allow_insecure_http_in_dev.")
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is not None and (
            addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        ):
            raise ConvyxError("Private or loopback IP Convyx URLs are not permitted.")
    return cleaned


class ConvyxAdapter:
    """Authenticated HTTP adapter for an independent Convyx collaboration service."""

    def __init__(
        self,
        config: ConvyxConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http_client = http_client

    def is_configured(self) -> bool:
        return bool(
            self._config.enabled
            and self._config.base_url.strip()
            and (self._config.service_token or "").strip()
        )

    def _base_url(self) -> str:
        return validate_convyx_url(
            self._config.base_url,
            allow_insecure_http=self._config.allow_insecure_http_in_dev,
        )

    def _headers(self, principal: RequestPrincipal | None = None) -> dict[str, str]:
        token = (self._config.service_token or "").strip()
        if not token:
            raise ConvyxError("Convyx service token is not configured.")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if principal is not None:
            headers["X-Yonder-Subject"] = principal.subject
            tenant_id = (principal.attributes.get("tenant_id") or "").strip()
            if tenant_id:
                headers["X-Yonder-Tenant-Id"] = tenant_id
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        principal: RequestPrincipal | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url()}{path}"
        headers = self._headers(principal)
        if self._http_client is not None:
            resp = await self._http_client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=self._config.timeout_seconds,
                follow_redirects=False,
            )
        else:
            async with httpx.AsyncClient(
                timeout=self._config.timeout_seconds,
                follow_redirects=False,
            ) as client:
                resp = await client.request(method, url, headers=headers, json=json_body)
        if resp.status_code >= 400:
            raise ConvyxError(f"Convyx service returned HTTP {resp.status_code}.")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ConvyxError("Convyx response must be a JSON object.")
        return payload

    async def status(self) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "enabled": False,
                "availability": "not_configured",
                "detail": "Convyx adapter is optional and not configured.",
            }
        try:
            caps = await self._request("GET", "/convyx/v1/capabilities")
        except (ConvyxError, httpx.HTTPError, ValueError) as exc:
            return {
                "enabled": True,
                "availability": "collection_failed",
                "detail": str(exc),
            }
        return {
            "enabled": True,
            "availability": "ready",
            "capabilities": caps,
        }

    async def handoff_task(
        self,
        principal: RequestPrincipal,
        *,
        task_id: str,
        summary: str,
        target_agent: str,
        context_scope: str = "project",
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise ConvyxError("Convyx adapter is not configured.")
        if not ID_RE.fullmatch(task_id) or not ID_RE.fullmatch(target_agent):
            raise ConvyxError("Invalid task_id or target_agent identifier.")
        return await self._request(
            "POST",
            "/convyx/v1/handoffs",
            principal=principal,
            json_body={
                "task_id": task_id,
                "summary": summary[:1000],
                "target_agent": target_agent,
                "context_scope": context_scope,
            },
        )

    async def get_handoff(
        self,
        principal: RequestPrincipal,
        handoff_id: str,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise ConvyxError("Convyx adapter is not configured.")
        if not ID_RE.fullmatch(handoff_id):
            raise ConvyxError("Invalid handoff_id.")
        return await self._request(
            "GET",
            f"/convyx/v1/handoffs/{handoff_id}",
            principal=principal,
        )
