"""Cross-family GPU occupancy must refuse llama.cpp vs Diffusers overlap."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.runtime.diffusers_manager import DiffusersRuntimeManager
from app.runtime.llama_cpp_manager import LlamaCppRuntimeManager
from app.runtime.occupancy import union_lane_counts, union_occupied_gpus
from yonder_configuration.models import (
    DiffusersWorkerRuntimeConfig,
    LlamaCppWorkerRuntimeConfig,
)


def test_union_occupied_gpus_merges_runtime_families(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"gguf")
    llama = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[tmp_path],
        )
    )
    diffusers = DiffusersRuntimeManager(
        DiffusersWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[tmp_path],
        )
    )
    llama.load(
        deployment_id="chat-a",
        model_definition_id="chat-def",
        runtime_configuration={
            "profile": "chat",
            "model_path": str(gguf),
            "model_identifier": "chat-a",
        },
        gpu_assignments=[0],
    )
    diffusers.load(
        deployment_id="image-a",
        model_definition_id="image-def",
        runtime_configuration={
            "profile": "image",
            "family": "sdxl",
            "model_path": str(tmp_path / "sdxl"),
        },
        gpu_assignments=[1],
    )
    assert union_lane_counts(llama, diffusers) == {0: 1, 1: 1}
    assert union_occupied_gpus(llama, diffusers, max_lanes_per_gpu=3) == set()
    assert union_occupied_gpus(llama, diffusers, max_lanes_per_gpu=1) == {0, 1}


def test_diffusers_shares_gpu_with_llama_under_lane_cap(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"gguf")
    llama = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[tmp_path],
            max_lanes_per_gpu=3,
        )
    )
    diffusers = DiffusersRuntimeManager(
        DiffusersWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[tmp_path],
            max_lanes_per_gpu=3,
        )
    )
    llama.load(
        deployment_id="chat-a",
        model_definition_id="chat-def",
        runtime_configuration={
            "profile": "chat",
            "model_path": str(gguf),
            "model_identifier": "chat-a",
        },
        gpu_assignments=[0],
    )
    record = diffusers.load(
        deployment_id="image-a",
        model_definition_id="image-def",
        runtime_configuration={
            "profile": "image",
            "family": "sdxl",
            "model_path": str(tmp_path / "sdxl"),
        },
        gpu_assignments=[0],
        already_occupied=union_occupied_gpus(llama, diffusers, max_lanes_per_gpu=3),
    )
    assert record["status"] == "ready"


def test_fourth_lane_on_same_gpu_is_refused(tmp_path: Path) -> None:
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"gguf")
    llama = LlamaCppRuntimeManager(
        LlamaCppWorkerRuntimeConfig(
            enabled=False,
            dry_run=True,
            allowed_model_roots=[tmp_path],
            max_lanes_per_gpu=3,
        )
    )
    for i in range(3):
        llama.load(
            deployment_id=f"chat-{i}",
            model_definition_id="chat-def",
            runtime_configuration={
                "profile": "chat",
                "model_path": str(gguf),
                "model_identifier": f"chat-{i}",
            },
            gpu_assignments=[0],
        )
    with pytest.raises(RuntimeError, match="lane cap"):
        llama.load(
            deployment_id="chat-3",
            model_definition_id="chat-def",
            runtime_configuration={
                "profile": "chat",
                "model_path": str(gguf),
                "model_identifier": "chat-3",
            },
            gpu_assignments=[0],
            already_occupied=union_occupied_gpus(llama, max_lanes_per_gpu=3),
        )
