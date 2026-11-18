"""Gateway-to-controller client; browser never receives deployment credentials."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx


class ControllerUnavailable(RuntimeError):
    pass


class AddonControllerClient:
    def __init__(self, url: str, token: str):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
        ):
            raise ValueError("Invalid controller service origin")
        if parsed.scheme == "http" and parsed.hostname not in (
            "addon-controller", "127.0.0.1", "localhost"
        ):
            raise ValueError("Controller plaintext is allowed only on local service networks")
        if len(token) < 32:
            raise ValueError("Controller token must be at least 32 characters")
        self.url = url.rstrip("/")
        self._token = token

    @classmethod
    def from_environment(cls) -> AddonControllerClient | None:
        url = os.getenv("YONDER_ADDON_CONTROLLER_URL", "").strip()
        token = os.getenv("YONDER_ADDON_CONTROLLER_TOKEN", "")
        if not url and not token:
            return None
        if not url or not token:
            raise ValueError("Controller URL and token must both be provided")
        return cls(url, token)

    async def request(
        self, method: str, path: str, body: dict | None = None
    ) -> dict:
        if method not in ("GET", "POST") or not path.startswith("/v1/"):
            raise ValueError("Unsupported controller request")
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                trust_env=False,
                follow_redirects=False,
                headers={"Authorization": "Bearer " + self._token},
            ) as client:
                response = await client.request(
                    method, self.url + path, json=body
                )
                if response.status_code >= 400:
                    raise ControllerUnavailable(
                        f"Controller rejected request ({response.status_code})"
                    )
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ControllerUnavailable("Controller cannot be reached or verified") from exc
