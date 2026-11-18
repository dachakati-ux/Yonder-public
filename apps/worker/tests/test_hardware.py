"""Hardware metric collection and normalization tests."""

from __future__ import annotations

from subprocess import CompletedProcess

from app.hardware import collector
from app.hardware.collector import (
    collect_gpus,
    collect_hardware_inventory,
    collect_host_metrics,
)
from yonder_contracts.hardware import MetricAvailability, MetricValue


def test_metric_value_available_and_unavailable() -> None:
    ok = MetricValue.available(42, unit="percent")
    assert ok.availability == MetricAvailability.AVAILABLE
    assert ok.value == 42

    missing = MetricValue.unavailable(
        MetricAvailability.NOT_SUPPORTED, message="no nvidia"
    )
    assert missing.value is None
    assert missing.availability == MetricAvailability.NOT_SUPPORTED


def test_metric_value_rejects_available_via_unavailable_helper() -> None:
    try:
        MetricValue.unavailable(MetricAvailability.AVAILABLE)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_host_metrics_are_real_not_fabricated() -> None:
    host = collect_host_metrics()
    assert host.memory_total_bytes.availability == MetricAvailability.AVAILABLE
    assert isinstance(host.memory_total_bytes.value, int)
    assert host.memory_total_bytes.value > 0
    assert host.cpu_count.availability in {
        MetricAvailability.AVAILABLE,
        MetricAvailability.PERMISSION_UNAVAILABLE,
        MetricAvailability.COLLECTION_FAILED,
    }
    assert host.hostname.availability == MetricAvailability.AVAILABLE


def test_host_metrics_report_cpu_collection_failure(monkeypatch) -> None:
    def fail_cpu_count(*_args, **_kwargs):
        raise PermissionError("sysctl denied")

    monkeypatch.setattr(collector.psutil, "cpu_count", fail_cpu_count)

    host = collect_host_metrics()

    assert host.cpu_count.availability == MetricAvailability.PERMISSION_UNAVAILABLE
    assert host.cpu_count.value is None
    assert "sysctl denied" in (host.cpu_count.message or "")


def test_gpu_discovery_does_not_fabricate_devices_without_nvidia() -> None:
    gpus, status, message, errors = collect_gpus()
    inventory = collect_hardware_inventory()

    if status == MetricAvailability.NOT_SUPPORTED:
        assert gpus == []
        assert inventory.gpus == []
        assert message is not None
        assert "nvidia-smi" in message.lower() or "NVIDIA" in message
    else:
        # If this host somehow has NVIDIA tooling, values must come from collection.
        assert status in {
            MetricAvailability.AVAILABLE,
            MetricAvailability.NOT_REPORTED,
            MetricAvailability.COLLECTION_FAILED,
        }
        for gpu in gpus:
            assert gpu.uuid.availability in {
                MetricAvailability.AVAILABLE,
                MetricAvailability.NOT_REPORTED,
                MetricAvailability.COLLECTION_FAILED,
            }


def test_inventory_notes_mention_mac_when_darwin() -> None:
    import platform

    inventory = collect_hardware_inventory()
    if platform.system() == "Darwin":
        assert any("macOS" in note for note in inventory.notes)
        assert inventory.gpu_discovery_status == MetricAvailability.NOT_SUPPORTED


def test_nvidia_query_parses_v100_fixture(monkeypatch) -> None:
    row = (
        "0, GPU-test-uuid, Tesla V100-SXM2-32GB, 41, 1024, 32768, "
        "55, 120.5, 300.0, 1380, 877, Enabled\n"
    )

    def fake_run(*_args, **_kwargs):
        return CompletedProcess(args=["nvidia-smi"], returncode=0, stdout=row, stderr="")

    monkeypatch.setattr(collector.subprocess, "run", fake_run)
    gpus, errors = collector._parse_nvidia_smi_query()

    assert errors == []
    assert len(gpus) == 1
    assert gpus[0].name.value == "Tesla V100-SXM2-32GB"
    assert gpus[0].uuid.value == "GPU-test-uuid"
    assert gpus[0].memory_total_bytes.value == 32768 * 1024 * 1024
