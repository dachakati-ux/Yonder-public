"""Worker registration recovery tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.registration.client import (
    WorkerRegistrationRequired,
    ensure_registered_heartbeat,
    send_heartbeat,
)


def _response(status_code: int, payload: dict[str, Any] | None = None) -> httpx.Response:
    request = httpx.Request("POST", "http://gateway.example/api/admin/workers/w1/heartbeat")
    return httpx.Response(status_code, json=payload or {}, request=request)


@pytest.mark.asyncio
async def test_send_heartbeat_raises_registration_required_on_404() -> None:
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=_response(404, {"error": "must register"}))

    with patch("app.registration.client.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(WorkerRegistrationRequired):
            await send_heartbeat(
                gateway_url="http://gateway.example",
                worker_id="w1",
                display_name="Worker 1",
                endpoint="http://worker:8090",
                hardware={"gpu_discovery_status": "not_reported"},
                ready=True,
            )


@pytest.mark.asyncio
async def test_ensure_registered_heartbeat_reregisters_after_404() -> None:
    heartbeat_404 = _response(404, {"error": "must register"})
    register_ok = _response(200, {"accepted": True})
    heartbeat_ok = _response(200, {"accepted": True, "status": "connected"})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[heartbeat_404, register_ok, heartbeat_ok])

    with patch("app.registration.client.httpx.AsyncClient", return_value=mock_client):
        result = await ensure_registered_heartbeat(
            gateway_url="http://gateway.example",
            worker_id="w1",
            display_name="Worker 1",
            endpoint="http://worker:8090",
            hardware={"gpu_discovery_status": "not_reported"},
            ready=True,
        )

    assert result["accepted"] is True
    assert mock_client.post.await_count == 3
    paths = [call.args[0] for call in mock_client.post.await_args_list]
    assert paths[0].endswith("/heartbeat")
    assert paths[1].endswith("/register")
    assert paths[2].endswith("/heartbeat")
