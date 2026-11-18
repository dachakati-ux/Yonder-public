"""Diffusers generation provider polls the deployment's worker endpoint."""

from __future__ import annotations

import httpx
import pytest
from app.providers.diffusers_generation import DiffusersGenerationProvider
from yonder_configuration.models import DiffusersProviderConfig
from yonder_contracts.generation import GenerationMode, GenerationRequest


@pytest.mark.asyncio
async def test_get_job_uses_bound_worker_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/diffusers-req1"):
            return httpx.Response(
                200,
                json={
                    "job_id": "diffusers-req1",
                    "status": "running",
                    "availability": "available",
                    "deployment_id": "dep-ai7",
                    "model_alias": "yonder-image",
                    "mode": "txt2img",
                    "prompt": "cyan skyline",
                    "progress": [
                        {
                            "job_id": "diffusers-req1",
                            "step": "denoise",
                            "message": "Denoising step 2/4",
                            "percent": 50.0,
                        }
                    ],
                    "assets": [],
                },
            )
        return httpx.Response(404, json={"detail": "missing"})

    transport = httpx.MockTransport(handler)
    provider = DiffusersGenerationProvider(
        DiffusersProviderConfig(worker_base_url="http://wrong-default:8090"),
        transport=transport,
    )
    provider.bind_job_route(
        "diffusers-req1",
        deployment_id="dep-ai7",
        worker_base_url="http://worker-ai7:8090",
    )
    job = await provider.get_job("diffusers-req1")
    assert job.status.value == "running"
    assert job.progress[-1].percent == 50.0
    assert any("worker-ai7:8090" in url for url in seen)
    assert not any("wrong-default" in url for url in seen)


@pytest.mark.asyncio
async def test_create_job_records_deployment_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "worker-ai3:8090" in str(request.url)
        return httpx.Response(
            200,
            json={
                "job_id": "diffusers-req2",
                "status": "queued",
                "availability": "available",
                "progress": [],
                "assets": [],
            },
        )

    provider = DiffusersGenerationProvider(
        DiffusersProviderConfig(worker_base_url="http://wrong-default:8090"),
        transport=httpx.MockTransport(handler),
    )
    provider.bind_job_route(
        "",
        deployment_id="dep-ai3",
        worker_base_url="http://worker-ai3:8090",
    )
    request = GenerationRequest(
        request_id="req2",
        model_alias="yonder-image",
        deployment_id="dep-ai3",
        mode=GenerationMode.TEXT_TO_IMAGE,
        prompt="hello",
    )
    job = await provider.create_job(request)
    assert job.job_id == "diffusers-req2"
    assert provider._job_deployments[job.job_id] == "dep-ai3"
