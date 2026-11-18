"""Hardware discovery for Yonder workers.

Never fabricate unavailable metrics. On hosts without NVIDIA tooling,
GPU fields are reported with explicit availability states.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psutil
from yonder_contracts.hardware import (
    GpuMetrics,
    HardwareInventory,
    HostMetrics,
    MetricAvailability,
    MetricValue,
    NetworkInterfaceMetrics,
    StorageMetrics,
)

_UNAVAILABLE_GPU_FIELDS = (
    "index",
    "uuid",
    "name",
    "utilization_percent",
    "memory_used_bytes",
    "memory_total_bytes",
    "memory_available_bytes",
    "temperature_c",
    "power_draw_watts",
    "power_limit_watts",
    "clock_graphics_mhz",
    "clock_memory_mhz",
    "ecc_status",
    "nvlink_state",
    "active_processes",
)


def _mv_available(value: float | int | str | bool, unit: str | None = None) -> MetricValue:
    return MetricValue.available(value, unit=unit)


def _mv_unavailable(
    availability: MetricAvailability,
    *,
    message: str | None = None,
    unit: str | None = None,
) -> MetricValue:
    return MetricValue.unavailable(availability, message=message, unit=unit)


def _collect_metric(
    collector: Callable[[], float | int | str | bool | None],
    *,
    unit: str | None = None,
) -> MetricValue:
    try:
        value = collector()
    except PermissionError as exc:
        return _mv_unavailable(
            MetricAvailability.PERMISSION_UNAVAILABLE,
            message=str(exc),
            unit=unit,
        )
    except Exception as exc:  # noqa: BLE001
        return _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED,
            message=str(exc),
            unit=unit,
        )
    if value is None:
        return _mv_unavailable(
            MetricAvailability.NOT_REPORTED,
            message="Collector returned no value",
            unit=unit,
        )
    return _mv_available(value, unit=unit)


def _cpu_model() -> MetricValue:
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return _mv_available(result.stdout.strip())
        elif system == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("model name"):
                        return _mv_available(line.split(":", 1)[1].strip())
        return _mv_available(platform.processor() or platform.machine())
    except OSError as exc:
        return _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED,
            message=str(exc),
        )


def _uptime_seconds() -> MetricValue:
    try:
        return _mv_available(int(time.time() - psutil.boot_time()), unit="seconds")
    except Exception as exc:  # noqa: BLE001
        return _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED,
            message=str(exc),
            unit="seconds",
        )


def _nvidia_driver_version() -> MetricValue:
    if shutil.which("nvidia-smi") is None:
        return _mv_unavailable(
            MetricAvailability.NOT_SUPPORTED,
            message="nvidia-smi not found on PATH",
        )
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return _mv_unavailable(
                MetricAvailability.COLLECTION_FAILED,
                message=result.stderr.strip() or "nvidia-smi failed",
            )
        version = result.stdout.strip().splitlines()[0].strip()
        return _mv_available(version)
    except Exception as exc:  # noqa: BLE001
        return _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED,
            message=str(exc),
        )


def _cuda_version() -> MetricValue:
    # Prefer nvidia-smi reported CUDA version when available; never invent one.
    if shutil.which("nvidia-smi") is None:
        return _mv_unavailable(
            MetricAvailability.NOT_SUPPORTED,
            message="CUDA version not reported because nvidia-smi is unavailable",
        )
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return _mv_unavailable(
                MetricAvailability.COLLECTION_FAILED,
                message=result.stderr.strip() or "nvidia-smi failed",
            )
        for line in result.stdout.splitlines():
            if "CUDA Version" in line:
                # Example: "| NVIDIA-SMI 535.xx    Driver Version: ...    CUDA Version: 12.2 |"
                part = line.split("CUDA Version:")[-1]
                version = part.split("|")[0].strip()
                if version:
                    return _mv_available(version)
        return _mv_unavailable(
            MetricAvailability.NOT_REPORTED,
            message="nvidia-smi did not include CUDA Version",
        )
    except Exception as exc:  # noqa: BLE001
        return _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED,
            message=str(exc),
        )


def _collect_storage() -> list[StorageMetrics]:
    mounts: list[StorageMetrics] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen:
            continue
        # Skip pseudo filesystems when obvious.
        if part.fstype in {"", "autofs", "devfs", "devtmpfs", "tmpfs", "proc", "sysfs"}:
            continue
        seen.add(part.mountpoint)
        try:
            usage = psutil.disk_usage(part.mountpoint)
            mounts.append(
                StorageMetrics(
                    mountpoint=part.mountpoint,
                    total_bytes=_mv_available(usage.total, unit="bytes"),
                    used_bytes=_mv_available(usage.used, unit="bytes"),
                    available_bytes=_mv_available(usage.free, unit="bytes"),
                    health=_mv_unavailable(
                        MetricAvailability.NOT_REPORTED,
                        message="Storage health sensors are not collected in Phase 1",
                    ),
                )
            )
        except PermissionError:
            mounts.append(
                StorageMetrics(
                    mountpoint=part.mountpoint,
                    total_bytes=_mv_unavailable(
                        MetricAvailability.PERMISSION_UNAVAILABLE,
                        message="Permission denied reading disk usage",
                        unit="bytes",
                    ),
                    used_bytes=_mv_unavailable(
                        MetricAvailability.PERMISSION_UNAVAILABLE,
                        unit="bytes",
                    ),
                    available_bytes=_mv_unavailable(
                        MetricAvailability.PERMISSION_UNAVAILABLE,
                        unit="bytes",
                    ),
                    health=_mv_unavailable(MetricAvailability.PERMISSION_UNAVAILABLE),
                )
            )
        except Exception as exc:  # noqa: BLE001
            mounts.append(
                StorageMetrics(
                    mountpoint=part.mountpoint,
                    total_bytes=_mv_unavailable(
                        MetricAvailability.COLLECTION_FAILED,
                        message=str(exc),
                        unit="bytes",
                    ),
                    used_bytes=_mv_unavailable(MetricAvailability.COLLECTION_FAILED, unit="bytes"),
                    available_bytes=_mv_unavailable(
                        MetricAvailability.COLLECTION_FAILED, unit="bytes"
                    ),
                    health=_mv_unavailable(MetricAvailability.COLLECTION_FAILED, message=str(exc)),
                )
            )
    return mounts


def _collect_network() -> list[NetworkInterfaceMetrics]:
    interfaces: list[NetworkInterfaceMetrics] = []
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    io = psutil.net_io_counters(pernic=True)
    for name, st in stats.items():
        address_list = []
        for addr in addrs.get(name, []):
            if addr.address:
                address_list.append(addr.address)
        io_counters = io.get(name)
        interfaces.append(
            NetworkInterfaceMetrics(
                name=name,
                is_up=_mv_available(bool(st.isup)),
                addresses=_mv_available(",".join(address_list) if address_list else ""),
                bytes_sent=_mv_available(io_counters.bytes_sent, unit="bytes")
                if io_counters
                else _mv_unavailable(MetricAvailability.NOT_REPORTED, unit="bytes"),
                bytes_recv=_mv_available(io_counters.bytes_recv, unit="bytes")
                if io_counters
                else _mv_unavailable(MetricAvailability.NOT_REPORTED, unit="bytes"),
                # Instantaneous throughput requires sampling; Phase 1 reports not_reported.
                throughput_sent_bps=_mv_unavailable(
                    MetricAvailability.NOT_REPORTED,
                    message="Throughput requires interval sampling; not enabled in Phase 1",
                    unit="bytes_per_second",
                ),
                throughput_recv_bps=_mv_unavailable(
                    MetricAvailability.NOT_REPORTED,
                    message="Throughput requires interval sampling; not enabled in Phase 1",
                    unit="bytes_per_second",
                ),
            )
        )
    return interfaces


def _empty_gpu(availability: MetricAvailability, message: str) -> dict[str, MetricValue]:
    return {
        field: _mv_unavailable(availability, message=message) for field in _UNAVAILABLE_GPU_FIELDS
    }


def _parse_nvidia_smi_query() -> tuple[list[GpuMetrics], list[str]]:
    errors: list[str] = []
    query = (
        "index,uuid,name,utilization.gpu,memory.used,memory.total,"
        "temperature.gpu,power.draw,power.limit,clocks.current.graphics,"
        "clocks.current.memory,ecc.mode.current"
    )
    result = subprocess.run(
        [
            "nvidia-smi",
            f"--query-gpu={query}",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "nvidia-smi query failed")
        return [], errors

    gpus: list[GpuMetrics] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 12:
            errors.append(f"Unexpected nvidia-smi row: {line!r}")
            continue

        def parse_number(raw: str, *, unit: str | None = None) -> MetricValue:
            if raw in {"[N/A]", "N/A", ""}:
                return _mv_unavailable(
                    MetricAvailability.NOT_REPORTED,
                    message="nvidia-smi reported N/A",
                    unit=unit,
                )
            try:
                if "." in raw:
                    return _mv_available(float(raw), unit=unit)
                return _mv_available(int(raw), unit=unit)
            except ValueError:
                return _mv_available(raw, unit=unit)

        memory_used_mib = parts[4]
        memory_total_mib = parts[5]
        used_metric = parse_number(memory_used_mib, unit="MiB")
        total_metric = parse_number(memory_total_mib, unit="MiB")
        if (
            used_metric.availability == MetricAvailability.AVAILABLE
            and total_metric.availability == MetricAvailability.AVAILABLE
            and isinstance(used_metric.value, (int, float))
            and isinstance(total_metric.value, (int, float))
        ):
            available = _mv_available(
                int((float(total_metric.value) - float(used_metric.value)) * 1024 * 1024),
                unit="bytes",
            )
            used_bytes = _mv_available(int(float(used_metric.value) * 1024 * 1024), unit="bytes")
            total_bytes = _mv_available(int(float(total_metric.value) * 1024 * 1024), unit="bytes")
        else:
            available = _mv_unavailable(MetricAvailability.NOT_REPORTED, unit="bytes")
            used_bytes = _mv_unavailable(MetricAvailability.NOT_REPORTED, unit="bytes")
            total_bytes = _mv_unavailable(MetricAvailability.NOT_REPORTED, unit="bytes")

        gpus.append(
            GpuMetrics(
                index=parse_number(parts[0]),
                uuid=_mv_available(parts[1]),
                name=_mv_available(parts[2]),
                utilization_percent=parse_number(parts[3], unit="percent"),
                memory_used_bytes=used_bytes,
                memory_total_bytes=total_bytes,
                memory_available_bytes=available,
                temperature_c=parse_number(parts[6], unit="celsius"),
                power_draw_watts=parse_number(parts[7], unit="watts"),
                power_limit_watts=parse_number(parts[8], unit="watts"),
                clock_graphics_mhz=parse_number(parts[9], unit="MHz"),
                clock_memory_mhz=parse_number(parts[10], unit="MHz"),
                ecc_status=_mv_available(parts[11])
                if parts[11] not in {"[N/A]", "N/A", ""}
                else _mv_unavailable(
                    MetricAvailability.NOT_REPORTED,
                    message="ECC mode not reported",
                ),
                nvlink_state=_mv_unavailable(
                    MetricAvailability.NOT_REPORTED,
                    message="NVLink state collection not implemented in Phase 1",
                ),
                active_processes=_mv_unavailable(
                    MetricAvailability.NOT_REPORTED,
                    message="Active process enumeration not implemented in Phase 1",
                ),
            )
        )
    return gpus, errors


def collect_gpus() -> tuple[list[GpuMetrics], MetricAvailability, str | None, list[str]]:
    if shutil.which("nvidia-smi") is None:
        message = (
            "No NVIDIA management tooling detected (nvidia-smi missing). "
            "This is expected on macOS development hosts. "
            "GPU inventory will be collected on the V100 worker when available."
        )
        return [], MetricAvailability.NOT_SUPPORTED, message, []

    try:
        gpus, errors = _parse_nvidia_smi_query()
        if errors and not gpus:
            return [], MetricAvailability.COLLECTION_FAILED, errors[0], errors
        if not gpus:
            return (
                [],
                MetricAvailability.NOT_REPORTED,
                "nvidia-smi ran but reported no GPUs",
                errors,
            )
        return gpus, MetricAvailability.AVAILABLE, None, errors
    except Exception as exc:  # noqa: BLE001
        return [], MetricAvailability.COLLECTION_FAILED, str(exc), [str(exc)]


def collect_host_metrics() -> HostMetrics:
    try:
        vm = psutil.virtual_memory()
        memory_total = _mv_available(vm.total, unit="bytes")
        memory_used = _mv_available(vm.used, unit="bytes")
        memory_available = _mv_available(vm.available, unit="bytes")
    except Exception as exc:  # noqa: BLE001
        memory_total = _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED, message=str(exc), unit="bytes"
        )
        memory_used = _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED, message=str(exc), unit="bytes"
        )
        memory_available = _mv_unavailable(
            MetricAvailability.COLLECTION_FAILED, message=str(exc), unit="bytes"
        )
    return HostMetrics(
        hostname=_mv_available(socket.gethostname()),
        operating_system=_mv_available(f"{platform.system()} {platform.release()}"),
        kernel_version=_mv_available(platform.version()),
        architecture=_mv_available(platform.machine()),
        cpu_model=_cpu_model(),
        cpu_count=_collect_metric(lambda: psutil.cpu_count(logical=True)),
        cpu_utilization_percent=_collect_metric(
            lambda: psutil.cpu_percent(interval=0.1), unit="percent"
        ),
        memory_total_bytes=memory_total,
        memory_used_bytes=memory_used,
        memory_available_bytes=memory_available,
        uptime_seconds=_uptime_seconds(),
        nvidia_driver_version=_nvidia_driver_version(),
        cuda_version=_cuda_version(),
        storage=_collect_storage(),
        network_interfaces=_collect_network(),
    )


def collect_hardware_inventory() -> HardwareInventory:
    host = collect_host_metrics()
    gpus, gpu_status, gpu_message, errors = collect_gpus()
    notes = [
        "Hardware values are collected from the local host only.",
        "Unavailable metrics use explicit availability states and are never fabricated.",
    ]
    if platform.system() == "Darwin":
        notes.append(
            "Running on macOS development host. NVIDIA V100 discovery is deferred "
            "until the target GPU server is available."
        )
    if os.environ.get("YONDER_HARDWARE_NOTE"):
        notes.append(os.environ["YONDER_HARDWARE_NOTE"])

    return HardwareInventory(
        collected_at=datetime.now(UTC),
        host=host,
        gpus=gpus,
        gpu_discovery_status=gpu_status,
        gpu_discovery_message=gpu_message,
        notes=notes,
        raw_collector_errors=errors,
    )


def inventory_as_dict() -> dict[str, Any]:
    return collect_hardware_inventory().model_dump(mode="json")
