"""EnsureReady capacity scheduler unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.routing.models import DeploymentRecord, DeploymentState, ModelDefinitionRecord
from app.routing.registry import RouteRegistry
from app.services.ensure_ready import EnsureDeploymentReady, EnsureReadyError
from yonder_configuration.models import CapacitySchedulerConfig
from yonder_contracts.errors import ErrorCode
from yonder_contracts.generation import GenerationMode
from yonder_contracts.hardware import MetricAvailability


class _FakeLifecycle:
    def __init__(self, routes: RouteRegistry) -> None:
        self.routes = routes
        self.loaded: list[dict[str, Any]] = []
        self.unloaded: list[str] = []

    async def load(self, **kwargs: Any) -> DeploymentRecord:
        model_id = kwargs["model_definition_id"]
        worker_id = kwargs["worker_id"]
        gpu_indexes = list(kwargs["gpu_indexes"])
        deployment_id = f"{model_id}-{worker_id}-g{gpu_indexes[0]}"
        record = DeploymentRecord(
            id=deployment_id,
            model_definition_id=model_id,
            worker_id=worker_id,
            provider="diffusers",
            status=DeploymentState.READY,
            gpu_assignments=gpu_indexes,
            runtime_configuration={"profile": "image", "base_url": "http://w"},
            loaded_at=datetime.now(UTC),
        )
        self.routes.upsert_deployment(record)
        self.loaded.append(kwargs)
        return record

    async def drain(self, deployment_id: str, **_kwargs: Any) -> DeploymentRecord:
        return self.routes.deployments[deployment_id]

    async def unload(self, deployment_id: str, **_kwargs: Any) -> DeploymentRecord:
        deployment = self.routes.deployments[deployment_id]
        deployment.status = DeploymentState.STOPPED
        self.unloaded.append(deployment_id)
        return deployment

    def set_alias(self, alias: str, deployment_id: str, **_kwargs: Any) -> None:
        from app.routing.models import ModelAliasRoute, RouteState

        self.routes.upsert_alias(
            ModelAliasRoute(
                alias=alias,
                deployment_id=deployment_id,
                state=RouteState.ENABLED,
            )
        )


def _metric(value: object, *, availability: str = "available", unit: str | None = None) -> dict:
    return {
        "availability": availability,
        "value": value if availability == "available" else None,
        "unit": unit,
        "message": None if availability == "available" else "unavailable",
    }


def _gpu_worker(worker_id: str = "worker-ai1") -> dict[str, Any]:
    total = 32 * (1024**3)
    available = 28 * (1024**3)
    return {
        worker_id: {
            "maintenance_state": False,
            "hardware_summary": {
                "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "gpu_discovery_status": MetricAvailability.AVAILABLE.value,
                "gpu_discovery_message": None,
                "notes": [],
                "raw_collector_errors": [],
                "host": {
                    "hostname": _metric("ai1"),
                    "operating_system": _metric("rhel"),
                    "kernel_version": _metric("4.18"),
                    "architecture": _metric("x86_64"),
                    "cpu_model": _metric("xeon"),
                    "cpu_count": _metric(72),
                    "cpu_utilization_percent": _metric(None, availability="not_reported"),
                    "memory_total_bytes": _metric(None, availability="not_reported"),
                    "memory_used_bytes": _metric(None, availability="not_reported"),
                    "memory_available_bytes": _metric(None, availability="not_reported"),
                    "uptime_seconds": _metric(None, availability="not_reported"),
                    "nvidia_driver_version": _metric("580"),
                    "cuda_version": _metric("12.6"),
                    "storage": [],
                    "network_interfaces": [],
                },
                "gpus": [
                    {
                        "index": _metric(0),
                        "uuid": _metric("GPU-0"),
                        "name": _metric("Tesla V100"),
                        "utilization_percent": _metric(0, unit="percent"),
                        "memory_used_bytes": _metric(0, unit="bytes"),
                        "memory_total_bytes": _metric(total, unit="bytes"),
                        "memory_available_bytes": _metric(available, unit="bytes"),
                        "temperature_c": _metric(None, availability="not_reported"),
                        "power_draw_watts": _metric(None, availability="not_reported"),
                        "power_limit_watts": _metric(None, availability="not_reported"),
                        "clock_graphics_mhz": _metric(None, availability="not_reported"),
                        "clock_memory_mhz": _metric(None, availability="not_reported"),
                        "ecc_status": _metric(None, availability="not_reported"),
                        "nvlink_state": _metric(None, availability="not_reported"),
                        "active_processes": _metric(None, availability="not_reported"),
                    }
                ],
            },
        }
    }


def _image_definition(model_id: str = "yonder-sdxl-turbo") -> ModelDefinitionRecord:
    return ModelDefinitionRecord(
        id=model_id,
        display_name=model_id,
        manifest={
            "id": model_id,
            "display_name": model_id,
            "runtime": {
                "provider": "diffusers",
                "profile": "image",
                "family": "sdxl",
                "model_identifier": model_id,
                "context_length": 77,
            },
            "requirements": {"minimum_gpu_memory_gb": 8},
            "source": {"type": "local", "path": "/models/sdxl"},
        },
    )


@pytest.mark.asyncio
async def test_ensure_loads_into_free_lane() -> None:
    routes = RouteRegistry()
    lifecycle = _FakeLifecycle(routes)
    definition = _image_definition()
    ensure = EnsureDeploymentReady(
        lifecycle=lifecycle,  # type: ignore[arg-type]
        routes=routes,
        workers=_gpu_worker(),
        definitions={definition.id: definition},
        config=CapacitySchedulerConfig(idle_evict_seconds=0),
    )
    result = await ensure.ensure(
        model_alias="yonder-image",
        mode=GenerationMode.TEXT_TO_IMAGE,
        principal_subject="tester",
        request_id="req1",
    )
    assert result.deployment_id
    assert lifecycle.loaded
    assert routes.get_alias("yonder-image") is not None


@pytest.mark.asyncio
async def test_ensure_evicts_idle_diffusers_when_lanes_full() -> None:
    routes = RouteRegistry()
    for i in range(3):
        routes.upsert_deployment(
            DeploymentRecord(
                id=f"img-{i}",
                model_definition_id="old-sdxl",
                worker_id="worker-ai1",
                provider="diffusers",
                status=DeploymentState.READY,
                gpu_assignments=[0],
                runtime_configuration={"profile": "image"},
                active_requests=0,
                loaded_at=datetime(2020, 1, 1, tzinfo=UTC),
                last_released_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
    lifecycle = _FakeLifecycle(routes)
    definition = _image_definition("yonder-new-image")
    ensure = EnsureDeploymentReady(
        lifecycle=lifecycle,  # type: ignore[arg-type]
        routes=routes,
        workers=_gpu_worker(),
        definitions={definition.id: definition},
        config=CapacitySchedulerConfig(idle_evict_seconds=0, max_lanes_per_gpu=3),
    )
    result = await ensure.ensure(
        model_definition_id=definition.id,
        mode=GenerationMode.TEXT_TO_IMAGE,
        principal_subject="tester",
        request_id="req2",
    )
    assert lifecycle.unloaded
    assert result.deployment_id.startswith("yonder-new-image")


@pytest.mark.asyncio
async def test_ensure_skips_locked_deployment() -> None:
    routes = RouteRegistry()
    for i in range(3):
        routes.upsert_deployment(
            DeploymentRecord(
                id=f"img-{i}",
                model_definition_id="old-sdxl",
                worker_id="worker-ai1",
                provider="diffusers",
                status=DeploymentState.READY,
                gpu_assignments=[0],
                runtime_configuration={"profile": "image", "locked": True},
                active_requests=0,
                last_released_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
    lifecycle = _FakeLifecycle(routes)
    definition = _image_definition("yonder-new-image")
    ensure = EnsureDeploymentReady(
        lifecycle=lifecycle,  # type: ignore[arg-type]
        routes=routes,
        workers=_gpu_worker(),
        definitions={definition.id: definition},
        config=CapacitySchedulerConfig(idle_evict_seconds=0),
    )
    with pytest.raises(EnsureReadyError) as exc:
        await ensure.ensure(
            model_definition_id=definition.id,
            mode=GenerationMode.TEXT_TO_IMAGE,
            principal_subject="tester",
            request_id="req3",
        )
    assert exc.value.code == ErrorCode.CAPACITY_EXCEEDED
    assert not lifecycle.unloaded


@pytest.mark.asyncio
async def test_ensure_reloads_when_gateway_ready_but_worker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteRegistry()
    stale = DeploymentRecord(
        id="yonder-sdxl-turbo-wai7-g1",
        model_definition_id="yonder-sdxl-turbo",
        worker_id="worker-ai7",
        provider="diffusers",
        status=DeploymentState.READY,
        gpu_assignments=[1],
        runtime_configuration={
            "profile": "image",
            "base_url": "http://worker-ai7:8090",
        },
    )
    routes.upsert_deployment(stale)
    workers = _gpu_worker("worker-ai7")
    workers["worker-ai7"]["endpoint"] = "http://worker-ai7:8090"
    lifecycle = _FakeLifecycle(routes)
    definition = _image_definition("yonder-sdxl-turbo")
    ensure = EnsureDeploymentReady(
        lifecycle=lifecycle,  # type: ignore[arg-type]
        routes=routes,
        workers=workers,
        definitions={definition.id: definition},
        config=CapacitySchedulerConfig(idle_evict_seconds=0),
    )

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            # Worker restarted — no Diffusers deployments in memory.
            return {"deployments": []}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            assert url.endswith("/v1/lifecycle/status")
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _Client())

    result = await ensure.ensure(
        model_alias="yonder-image",
        mode=GenerationMode.TEXT_TO_IMAGE,
        principal_subject="tester",
        request_id="req-stale",
    )
    assert stale.status == DeploymentState.STOPPED
    assert lifecycle.loaded
    assert result.deployment_id.startswith("yonder-sdxl-turbo")
    assert any("missing on worker" in (p.get("message") or "") for p in result.progress)


@pytest.mark.asyncio
async def test_ensure_credits_vram_after_evict_when_inventory_stale() -> None:
    """Stale low free-VRAM after unload must not block the next load."""
    routes = RouteRegistry()
    # Large occupant claiming the only GPU; free VRAM looks too low for a new image model.
    routes.upsert_deployment(
        DeploymentRecord(
            id="big-chat",
            model_definition_id="yonder-chat-large",
            worker_id="worker-ai1",
            provider="llama-cpp",
            status=DeploymentState.READY,
            gpu_assignments=[0],
            runtime_configuration={"profile": "chat"},
            active_requests=0,
            loaded_at=datetime(2020, 1, 1, tzinfo=UTC),
            last_released_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    workers = _gpu_worker()
    # Only 4 GiB free — not enough for 8 GiB image model until unload credit.
    workers["worker-ai1"]["hardware_summary"]["gpus"][0]["memory_available_bytes"] = (
        _metric(4 * (1024**3), unit="bytes")
    )
    workers["worker-ai1"]["hardware_summary"]["gpus"][0]["memory_used_bytes"] = _metric(
        28 * (1024**3), unit="bytes"
    )
    lifecycle = _FakeLifecycle(routes)
    chat_def = ModelDefinitionRecord(
        id="yonder-chat-large",
        display_name="chat-large",
        manifest={
            "id": "yonder-chat-large",
            "display_name": "chat-large",
            "runtime": {
                "provider": "llama-cpp",
                "profile": "chat",
                "family": "qwen",
                "model_identifier": "chat",
                "context_length": 8192,
            },
            "requirements": {"minimum_gpu_memory_gb": 24},
            "source": {"type": "local", "path": "/models/chat"},
        },
    )
    image_def = _image_definition("yonder-new-image")
    ensure = EnsureDeploymentReady(
        lifecycle=lifecycle,  # type: ignore[arg-type]
        routes=routes,
        workers=workers,
        definitions={chat_def.id: chat_def, image_def.id: image_def},
        config=CapacitySchedulerConfig(
            idle_evict_seconds=0,
            max_lanes_per_gpu=3,
            chat_protect_sole_lane=False,
            never_evict_aliases=[],
        ),
    )
    result = await ensure.ensure(
        model_definition_id=image_def.id,
        mode=GenerationMode.TEXT_TO_IMAGE,
        principal_subject="tester",
        request_id="req-vram",
    )
    assert "big-chat" in lifecycle.unloaded
    assert result.deployment_id.startswith("yonder-new-image")
    free = workers["worker-ai1"]["hardware_summary"]["gpus"][0]["memory_available_bytes"][
        "value"
    ]
    # 4 GiB + 24 GiB credit from unloaded chat.
    assert free >= 28 * (1024**3)


@pytest.mark.asyncio
async def test_ensure_demotes_sole_chat_down_ladder() -> None:
    routes = RouteRegistry()
    from app.routing.models import ModelAliasRoute, RouteState

    routes.upsert_deployment(
        DeploymentRecord(
            id="chat-large",
            model_definition_id="yonder-qwen36-35b-a3b-q4",
            worker_id="worker-ai1",
            provider="llama-cpp",
            status=DeploymentState.READY,
            gpu_assignments=[0],
            runtime_configuration={"profile": "chat"},
            active_requests=0,
            loaded_at=datetime(2020, 1, 1, tzinfo=UTC),
            last_released_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    routes.upsert_alias(
        ModelAliasRoute(
            alias="yonder-chat",
            deployment_id="chat-large",
            state=RouteState.ENABLED,
        )
    )
    workers = _gpu_worker()
    # Not enough free VRAM for 8 GiB image until chat is demoted/unloaded.
    workers["worker-ai1"]["hardware_summary"]["gpus"][0]["memory_available_bytes"] = (
        _metric(4 * (1024**3), unit="bytes")
    )
    workers["worker-ai1"]["hardware_summary"]["gpus"][0]["memory_used_bytes"] = _metric(
        28 * (1024**3), unit="bytes"
    )
    lifecycle = _FakeLifecycle(routes)

    def _chat_def(model_id: str, gb: int) -> ModelDefinitionRecord:
        return ModelDefinitionRecord(
            id=model_id,
            display_name=model_id,
            manifest={
                "id": model_id,
                "display_name": model_id,
                "runtime": {
                    "provider": "llama-cpp",
                    "profile": "chat",
                    "family": "qwen",
                    "model_identifier": model_id,
                    "context_length": 8192,
                },
                "requirements": {"minimum_gpu_memory_gb": gb},
                "source": {"type": "local", "path": f"/models/{model_id}"},
            },
        )

    large = _chat_def("yonder-qwen36-35b-a3b-q4", 24)
    small = _chat_def("yonder-nemotron-3-nano-4b", 6)
    image = _image_definition("yonder-sdxl-turbo")
    ensure = EnsureDeploymentReady(
        lifecycle=lifecycle,  # type: ignore[arg-type]
        routes=routes,
        workers=workers,
        definitions={large.id: large, small.id: small, image.id: image},
        config=CapacitySchedulerConfig(
            idle_evict_seconds=0,
            max_lanes_per_gpu=3,
            chat_protect_sole_lane=True,
            chat_alias="yonder-chat",
            chat_demotion_ladder=(
                "yonder-qwen36-35b-a3b-q4",
                "yonder-nemotron-3-nano-4b",
            ),
            never_evict_aliases=("yonder-chat",),
        ),
    )
    result = await ensure.ensure(
        model_definition_id=image.id,
        mode=GenerationMode.TEXT_TO_IMAGE,
        principal_subject="tester",
        request_id="req-demote",
    )
    assert "chat-large" in lifecycle.unloaded
    assert any(
        item.get("model_definition_id") == "yonder-nemotron-3-nano-4b"
        for item in lifecycle.loaded
    )
    assert routes.get_alias("yonder-chat") is not None
    assert result.deployment_id.startswith("yonder-sdxl-turbo")
