"""Worker llama.cpp runtime manager tests (dry-run, no real processes)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app.api.runtime_proxy as runtime_proxy_module
import app.runtime.llama_cpp_manager as llama_cpp_module
import httpx
import pytest
from app.runtime.llama_cpp_manager import LlamaCppRuntimeManager
from fastapi.testclient import TestClient
from yonder_configuration.models import LlamaCppWorkerRuntimeConfig


def test_live_vram_preflight_is_ordered_and_reserves_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(vram_reserve_gb=1.0)
    )
    monkeypatch.setattr(
        llama_cpp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="0, 20000\n1, 26000\n",
            stderr="",
        ),
    )

    split = manager._preflight_vram(
        gpu_assignments=[1, 0],
        runtime_configuration={"minimum_gpu_memory_gb": 6},
    )

    assert split == [24976, 18976]


def test_live_vram_preflight_refuses_external_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(vram_reserve_gb=1.0)
    )
    monkeypatch.setattr(
        llama_cpp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="0, 7000\n",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="plus 1024 MiB reserved"):
        manager._preflight_vram(
            gpu_assignments=[0],
            runtime_configuration={"minimum_gpu_memory_gb": 6},
        )


def test_two_gpu_process_uses_selected_devices_and_safe_layer_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"binary")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

        def send_signal(self, _signal):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(args, **kwargs):
        captured["args"] = list(args)
        captured["env"] = dict(kwargs["env"])
        return FakeProcess()

    monkeypatch.setattr(
        llama_cpp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="0, 20000\n1, 26000\n2, 32000\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(llama_cpp_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(LlamaCppRuntimeManager, "_wait_healthy", lambda *args: None)

    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=True,
            binary_path=binary,
            library_path=None,
            allowed_model_roots=[tmp_path],
            vram_reserve_gb=1.0,
            multi_gpu_split_mode="layer",
        )
    )
    result = manager.load(
        deployment_id="dep-two",
        model_definition_id="model-two",
        runtime_configuration={
            "model_identifier": "model-two",
            "model_path": str(model),
            "profile": "chat",
            "minimum_gpu_memory_gb": 6,
        },
        gpu_assignments=[1, 0],
    )

    assert result["status"] == "ready"
    assert captured["env"]["CUDA_VISIBLE_DEVICES"] == "1,0"
    args = captured["args"]
    assert args[args.index("--split-mode") + 1] == "layer"
    assert args[args.index("--tensor-split") + 1] == "24976,18976"
    assert "2" not in captured["env"]["CUDA_VISIBLE_DEVICES"]


def test_load_uses_runtime_configuration_context_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"binary")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 99

        def poll(self):
            return None

        def send_signal(self, _signal):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(args, **kwargs):
        captured["args"] = list(args)
        return FakeProcess()

    monkeypatch.setattr(
        llama_cpp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="0, 30000\n", stderr=""
        ),
    )
    monkeypatch.setattr(llama_cpp_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(LlamaCppRuntimeManager, "_wait_healthy", lambda *args: None)

    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=True,
            binary_path=binary,
            library_path=None,
            allowed_model_roots=[tmp_path],
            ctx_size=8192,
        )
    )
    manager.load(
        deployment_id="dep-ctx",
        model_definition_id="model-ctx",
        runtime_configuration={
            "model_identifier": "model-ctx",
            "model_path": str(model),
            "profile": "chat",
            "context_length": 32768,
            "minimum_gpu_memory_gb": 6,
        },
        gpu_assignments=[0],
    )
    args = captured["args"]
    assert args[args.index("--ctx-size") + 1] == "32768"


def test_load_uses_manifest_binary_path_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_binary = tmp_path / "runtimes" / "stock" / "llama-server"
    default_binary.parent.mkdir(parents=True)
    default_binary.write_bytes(b"stock")
    override_binary = tmp_path / "runtimes" / "prismml" / "llama-server"
    override_binary.parent.mkdir(parents=True)
    override_binary.write_bytes(b"prismml")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 42

        def poll(self):
            return None

        def send_signal(self, _signal):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(args, **kwargs):
        captured["args"] = list(args)
        return FakeProcess()

    monkeypatch.setattr(
        llama_cpp_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="0, 30000\n", stderr=""
        ),
    )
    monkeypatch.setattr(llama_cpp_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(LlamaCppRuntimeManager, "_wait_healthy", lambda *args: None)

    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=True,
            binary_path=default_binary,
            library_path=None,
            allowed_model_roots=[tmp_path],
            allowed_runtime_roots=[tmp_path / "runtimes"],
        )
    )
    manager.load(
        deployment_id="dep-bin",
        model_definition_id="model-bin",
        runtime_configuration={
            "model_identifier": "model-bin",
            "model_path": str(model),
            "profile": "chat",
            "binary_path": str(override_binary),
            "minimum_gpu_memory_gb": 6,
        },
        gpu_assignments=[0],
    )
    args = captured["args"]
    assert args[0] == str(override_binary)


def test_load_rejects_binary_outside_runtime_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_binary = tmp_path / "runtimes" / "stock" / "llama-server"
    default_binary.parent.mkdir(parents=True)
    default_binary.write_bytes(b"stock")
    outside = tmp_path / "evil" / "llama-server"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"evil")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"gguf")

    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=True,
            binary_path=default_binary,
            library_path=None,
            allowed_model_roots=[tmp_path],
            allowed_runtime_roots=[tmp_path / "runtimes"],
        )
    )
    with pytest.raises(RuntimeError, match="outside allowed runtime roots"):
        manager.load(
            deployment_id="dep-evil",
            model_definition_id="model-evil",
            runtime_configuration={
                "model_identifier": "model-evil",
                "model_path": str(model),
                "profile": "chat",
                "binary_path": str(outside),
                "minimum_gpu_memory_gb": 6,
            },
            gpu_assignments=[0],
        )


def test_manager_dry_run_load_unload_and_gpu_conflict(tmp_path: Path) -> None:
    model_root = tmp_path / "model-bundles"
    model_root.mkdir()
    model_path = model_root / "a.gguf"
    model_path.write_bytes(b"gguf")
    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            base_port=9100,
            allowed_model_roots=[model_root],
        )
    )
    first = manager.load(
        deployment_id="dep-a",
        model_definition_id="model-a",
        runtime_configuration={
            "model_identifier": "alias-a",
            "model_path": str(model_path),
            "profile": "chat",
        },
        gpu_assignments=[0],
    )
    assert first["status"] == "ready"
    assert first["base_url"].startswith("http://127.0.0.1:9100")
    assert manager.endpoint_for("dep-a") == first["base_url"]
    assert manager.endpoint_for("missing") is None
    assert first["gpu_assignments"] == [0]

    # Same GPU is allowed up to max_lanes_per_gpu (default 3); the 4th
    # lane on GPU 0 must be rejected at the cap.
    for extra in ("dep-lane-2", "dep-lane-3"):
        manager.load(
            deployment_id=extra,
            model_definition_id=f"model-{extra}",
            runtime_configuration={"model_identifier": extra},
            gpu_assignments=[0],
        )
    with pytest.raises(RuntimeError, match="lane cap"):
        manager.load(
            deployment_id="dep-over-cap",
            model_definition_id="model-over-cap",
            runtime_configuration={"model_identifier": "alias-over"},
            gpu_assignments=[0],
        )
    for extra in ("dep-lane-2", "dep-lane-3"):
        manager.unload(extra)

    second = manager.load(
        deployment_id="dep-b",
        model_definition_id="model-b",
        runtime_configuration={"model_identifier": "alias-b"},
        gpu_assignments=[1],
    )
    assert second["gpu_assignments"] == [1]

    unloaded = manager.unload("dep-a")
    assert unloaded["status"] == "stopped"
    assert manager.endpoint_for("dep-a") is None

    again = manager.load(
        deployment_id="dep-c",
        model_definition_id="model-c",
        runtime_configuration={"model_identifier": "alias-c"},
        gpu_assignments=[0],
    )
    assert again["status"] == "ready"


def test_manager_refuses_maintenance(tmp_path: Path) -> None:
    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[tmp_path],
        )
    )
    with pytest.raises(RuntimeError, match="maintenance"):
        manager.load(
            deployment_id="dep-m",
            model_definition_id="model-m",
            runtime_configuration={},
            gpu_assignments=[0],
            maintenance=True,
        )


def test_manager_rejects_path_outside_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[allowed],
        )
    )
    with pytest.raises(RuntimeError, match="outside allowed model roots"):
        manager.load(
            deployment_id="dep-x",
            model_definition_id="model-x",
            runtime_configuration={
                "model_path": "/tmp/evil.gguf",
                "profile": "chat",
            },
            gpu_assignments=[0],
        )


def test_manager_rejects_unsupported_profile(tmp_path: Path) -> None:
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    manager = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[tmp_path],
        )
    )
    with pytest.raises(RuntimeError, match="Unsupported llama.cpp profile"):
        manager.load(
            deployment_id="dep-p",
            model_definition_id="model-p",
            runtime_configuration={
                "model_path": str(model),
                "profile": "image",
            },
            gpu_assignments=[0],
        )


def test_lifecycle_api_uses_manager_when_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv(
        "YONDER_CONFIG_FILE",
        str(root / "config/environments/development.yaml"),
    )
    monkeypatch.setenv("YONDER_LLAMA_CPP_RUNTIME__DRY_RUN", "true")
    monkeypatch.setenv(
        "YONDER_WORKER_PUBLIC_ENDPOINT", "http://worker-ai3.test:8090"
    )

    from app.main import create_app

    client = TestClient(create_app())
    load = client.post(
        "/v1/lifecycle/load",
        json={
            "deployment_id": "dep-1",
            "model_definition_id": "model-1",
            "operation": "load",
            "gpu_assignments": [0],
            "runtime_configuration": {"model_identifier": "test-model"},
        },
    )
    assert load.status_code == 200, load.text
    body = load.json()
    assert body["accepted"] is True
    assert body["base_url"] == "http://worker-ai3.test:8090/v1/runtime/dep-1"
    assert body["deployment"]["base_url"] == body["base_url"]
    assert body["deployment"]["status"] == "ready"

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b'{"status":"ok"}'),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        runtime_proxy_module,
        "_runtime_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    proxied_health = client.get("/v1/runtime/dep-1/health")
    assert proxied_health.status_code == 200
    assert proxied_health.json() == {"status": "ok"}

    missing_proxy = client.get("/v1/runtime/missing/health")
    assert missing_proxy.status_code == 404

    unsupported_proxy = client.get("/v1/runtime/dep-1/admin")
    assert unsupported_proxy.status_code == 404

    # GPU 0 accepts lanes up to max_lanes_per_gpu (default 3); the lane
    # over the cap is rejected with 409.
    for index in (2, 3):
        lane = client.post(
            "/v1/lifecycle/load",
            json={
                "deployment_id": f"dep-{index}",
                "model_definition_id": f"model-{index}",
                "operation": "load",
                "gpu_assignments": [0],
                "runtime_configuration": {"model_identifier": f"lane-{index}"},
            },
        )
        assert lane.status_code == 200, lane.text
    conflict = client.post(
        "/v1/lifecycle/load",
        json={
            "deployment_id": "dep-over-cap",
            "model_definition_id": "model-over-cap",
            "operation": "load",
            "gpu_assignments": [0],
            "runtime_configuration": {"model_identifier": "over-cap"},
        },
    )
    assert conflict.status_code == 409

    unload = client.post(
        "/v1/lifecycle/unload",
        json={"deployment_id": "dep-1", "operation": "unload"},
    )
    assert unload.status_code == 200
    assert unload.json()["deployment"]["status"] == "stopped"

    status = client.get("/v1/status")
    assert status.status_code == 200
    assert status.json()["runtime"]["status"] == "llama_cpp"
