"""VM/microVM provisioning adapter contract and scoped Proxmox adapter (M05.3/M05.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx

DIGEST_RE = re.compile(r"^[a-z0-9._-]+@sha256:[a-f0-9]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")


class ProvisionerError(RuntimeError):
    """Raised when VM/microVM provisioning fails or violates isolation policy."""


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    project_id: str
    owner_subject: str
    tenant_id: str | None
    template_digest: str
    cpu_cores: int
    memory_mb: int
    ttl_seconds: int
    network_profile: Literal["none", "isolated_vlan"] = "none"

    def validate(self) -> None:
        if not DIGEST_RE.fullmatch(self.template_digest):
            raise ProvisionerError("Template must be pinned by immutable @sha256 digest")
        if not (1 <= self.cpu_cores <= 4):
            raise ProvisionerError("cpu_cores must be between 1 and 4")
        if not (256 <= self.memory_mb <= 8192):
            raise ProvisionerError("memory_mb must be between 256 and 8192")
        if not (60 <= self.ttl_seconds <= 14400):
            raise ProvisionerError("ttl_seconds must be between 60 and 14400")


class WorkspaceProvisioner(Protocol):
    backend_name: str

    async def provision_session(self, spec: SessionSpec) -> str: ...

    async def terminate_session(self, session_id: str, backend_ref: str) -> None: ...

    async def execute_in_session(
        self,
        session_id: str,
        backend_ref: str,
        command: list[str],
        *,
        timeout_seconds: int = 10,
    ) -> dict[str, object]: ...


class ProxmoxAdapter:
    """Scoped Proxmox VE adapter using least-privilege API tokens & isolated pools."""

    backend_name = "proxmox_vm"

    def __init__(
        self,
        *,
        api_url: str,
        api_token_id: str,
        api_token_secret: str,
        node: str,
        pool: str,
        isolated_bridge: str = "vmbr1",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(api_url.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ProvisionerError("Proxmox api_url must be HTTPS without embedded credentials")
        if api_token_id.startswith("root@"):
            raise ProvisionerError("Proxmox adapter refuses root@* tokens; use a scoped role")
        if "!" not in api_token_id or not api_token_secret.strip():
            raise ProvisionerError("Valid scoped PVEAPIToken id and secret required")
        if not SAFE_ID_RE.fullmatch(node) or not SAFE_ID_RE.fullmatch(pool):
            raise ProvisionerError("Invalid Proxmox node or pool identifier")
        self.api_url = api_url.strip().rstrip("/")
        self.auth_header = f"PVEAPIToken={api_token_id}={api_token_secret}"
        self.node = node
        self.pool = pool
        self.isolated_bridge = isolated_bridge
        self._http_client = http_client

    async def _post(self, path: str, data: dict[str, str | int]) -> dict:
        url = f"{self.api_url}{path}"
        headers = {"Authorization": self.auth_header}
        if self._http_client is not None:
            resp = await self._http_client.post(
                url, headers=headers, data=data, follow_redirects=False, timeout=15.0
            )
        else:
            async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
                resp = await client.post(url, headers=headers, data=data)
        if resp.status_code >= 400:
            raise ProvisionerError(f"Proxmox API rejected request (HTTP {resp.status_code})")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ProvisionerError("Malformed Proxmox API response")
        return payload

    async def provision_session(self, spec: SessionSpec) -> str:
        spec.validate()
        # Deterministic numeric VMID derived from session_id within high unprivileged range.
        vmid = 100000 + (int.from_bytes(spec.session_id.encode("utf-8")[:4], "big") % 800000)
        await self._post(
            f"/api2/json/nodes/{self.node}/qemu",
            {
                "vmid": vmid,
                "name": f"yonder-{spec.session_id}",
                "pool": self.pool,
                "cores": spec.cpu_cores,
                "memory": spec.memory_mb,
                "net0": f"virtio,bridge={self.isolated_bridge},firewall=1",
                "tags": f"yonder;{spec.project_id}",
            },
        )
        return f"pve://{self.node}/{vmid}"

    async def terminate_session(self, session_id: str, backend_ref: str) -> None:
        if not backend_ref.startswith(f"pve://{self.node}/"):
            raise ProvisionerError("Invalid Proxmox backend reference")
        vmid = backend_ref.rsplit("/", 1)[-1]
        if not vmid.isdigit():
            raise ProvisionerError("Invalid Proxmox VMID")
        await self._post(f"/api2/json/nodes/{self.node}/qemu/{vmid}/status/stop", {})

    async def execute_in_session(
        self,
        session_id: str,
        backend_ref: str,
        command: list[str],
        *,
        timeout_seconds: int = 10,
    ) -> dict[str, object]:
        if not backend_ref.startswith(f"pve://{self.node}/"):
            raise ProvisionerError("Invalid Proxmox backend reference")
        if not command or len(command) > 32:
            raise ProvisionerError("Command argv must contain 1..32 arguments")
        if not (1 <= timeout_seconds <= 30):
            raise ProvisionerError("timeout_seconds must be between 1 and 30")
        vmid = backend_ref.rsplit("/", 1)[-1]
        if not vmid.isdigit():
            raise ProvisionerError("Invalid Proxmox VMID")
        payload = await self._post(
            f"/api2/json/nodes/{self.node}/qemu/{vmid}/agent/exec",
            {"command": command[0], "input-data": " ".join(command[1:])},
        )
        return {"session_id": session_id, "vmid": vmid, "result": payload.get("data")}
