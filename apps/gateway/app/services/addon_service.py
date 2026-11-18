"""Separate-service add-on contracts and strictly validated health probing.

An add-on endpoint must be provisioned by an administrator, never by a browser
request or untrusted GitHub catalog. No secrets are returned to API callers.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.addon_contract import ADDON_ID, SEMVER, SUPPORTED_API_VERSION


class ServiceEndpoint(BaseModel):
    """Pre-provisioned, authenticated remote service location."""

    model_config = ConfigDict(extra="forbid")
    addon_id: str
    base_url: str
    bearer_token: str = Field(min_length=24, repr=False, exclude=True)
    expected_service_id: str

    @field_validator("addon_id", "expected_service_id")
    @classmethod
    def valid_identity(cls, value: str) -> str:
        if not ADDON_ID.fullmatch(value):
            raise ValueError("Invalid service or add-on identifier")
        return value

    @field_validator("base_url")
    @classmethod
    def safe_destination(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Service endpoint must be an HTTPS origin on port 443")
        host = parsed.hostname.lower().strip(".")
        if host in ("localhost", "localhost.localdomain"):
            raise ValueError("Loopback service URLs are not accepted")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or not all(
                label and label.replace("-", "").isalnum()
                for label in host.split(".")
            ):
                raise ValueError("Invalid service host") from None
        else:
            if not address.is_global:
                raise ValueError("Private, loopback, link-local and reserved IPs are blocked")
        # Administrator must additionally enforce an outbound network allowlist.
        return value.rstrip("/")


class ServiceCapabilities(BaseModel):
    """Actual response to a versioned, read-only add-on handshake."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    service_id: str
    api_version: int
    version: str
    capabilities: list[str] = Field(max_length=30)
    health: str

    @field_validator("addon_id", "service_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not ADDON_ID.fullmatch(value):
            raise ValueError("Invalid add-on service identity")
        return value

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if not SEMVER.fullmatch(value):
            raise ValueError("Invalid service version")
        return value

    @field_validator("capabilities")
    @classmethod
    def valid_capabilities(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Duplicate capabilities")
        if any(not ADDON_ID.fullmatch(cap.replace("_", "-")) for cap in value):
            raise ValueError("Invalid advertised capability")
        return value

    @field_validator("health")
    @classmethod
    def valid_health(cls, value: str) -> str:
        if value not in {"ready", "degraded", "unavailable"}:
            raise ValueError("Invalid health state")
        return value


class ServiceProbeError(ValueError):
    pass


async def probe_service(endpoint: ServiceEndpoint) -> dict[str, object]:
    """Read-only handshake; never accepts a URL from the requesting browser.

    TLS certificate validation is mandatory; redirects, proxies from environment
    variables, and arbitrary endpoint paths are disabled.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            verify=True,
        ) as client:
            response = await client.get(
                endpoint.base_url + "/addon/v1/capabilities",
                headers={"Authorization": "Bearer " + endpoint.bearer_token},
            )
            if response.status_code != 200:
                raise ServiceProbeError("Service did not accept the handshake")
            if len(response.content) > 16_384:
                raise ServiceProbeError("Service response exceeds metadata limit")
            payload = ServiceCapabilities.model_validate(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ServiceProbeError):
            raise
        raise ServiceProbeError("Could not verify add-on service") from exc
    if payload.addon_id != endpoint.addon_id:
        raise ServiceProbeError("Service add-on identity mismatch")
    if payload.service_id != endpoint.expected_service_id:
        raise ServiceProbeError("Service identity mismatch")
    if payload.api_version != SUPPORTED_API_VERSION:
        raise ServiceProbeError("Incompatible add-on service API version")
    return {
        "addon_id": payload.addon_id,
        "service_id": payload.service_id,
        "version": payload.version,
        "api_version": payload.api_version,
        "capabilities": payload.capabilities,
        "health": payload.health,
        "trusted": True,
    }
