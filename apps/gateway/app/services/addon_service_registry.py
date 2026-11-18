"""External add-on service configuration kept outside public manifests.

File format is intentionally narrow, with no browser-supplied URL or token.
Use an administrator-mounted file (0600 recommended). Do not return its bytes
to the frontend or log them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.services.addon_service import ServiceEndpoint, ServiceProbeError, probe_service


class ServiceConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    endpoints: list[ServiceEndpoint] = Field(default_factory=list, max_length=30)


class AddonServiceRegistry:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.endpoints: dict[str, ServiceEndpoint] = {}

    def reload(self) -> dict[str, object]:
        # An absent file means no external services are configured.
        if not self.config_path.exists():
            self.endpoints = {}
            return {"status": "not_configured", "count": 0}
        if self.config_path.is_symlink():
            raise ServiceProbeError("Service configuration must not be a symlink")
        stat = self.config_path.stat()
        if stat.st_size > 100_000:
            raise ServiceProbeError("Service configuration too large")
        if os.name == "posix" and stat.st_mode & 0o077:
            raise ServiceProbeError("Service configuration must be private (mode 0600)")
        try:
            data = ServiceConfigFile.model_validate(
                json.loads(self.config_path.read_text(encoding="utf-8"))
            )
        except (ValueError, OSError) as exc:
            raise ServiceProbeError("Invalid service configuration") from exc
        endpoints: dict[str, ServiceEndpoint] = {}
        for endpoint in data.endpoints:
            if endpoint.addon_id in endpoints:
                raise ServiceProbeError("Duplicate add-on service configuration")
            endpoints[endpoint.addon_id] = endpoint
        self.endpoints = endpoints
        return {"status": "configured", "count": len(endpoints)}

    async def health(self, addon_id: str, *, installed: bool) -> dict[str, object]:
        if not installed:
            return {"addon_id": addon_id, "status": "not_installed"}
        endpoint = self.endpoints.get(addon_id)
        if endpoint is None:
            return {"addon_id": addon_id, "status": "not_configured"}
        try:
            result = await probe_service(endpoint)
            return {"addon_id": addon_id, "status": "verified", "service": result}
        except ServiceProbeError:
            return {"addon_id": addon_id, "status": "unavailable"}
