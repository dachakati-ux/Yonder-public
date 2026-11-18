# V100 Bootstrap

This document records discovered hardware and compatibility details for the first
GPU worker.

## Bootstrap development host (2026-07-22)

Yonder is being developed on a Mac until the target server is available.

| Field | Observed value |
| --- | --- |
| Role | Local development laptop (not the V100 worker) |
| CPU | Apple M5 Max |
| Logical CPUs | 18 |
| Memory | 128 GiB |
| OS | macOS 26.5.2 (Darwin 25.5.0) |
| Architecture | arm64 |
| `nvidia-smi` | Not found |
| NVIDIA driver | Not present |
| CUDA | Not present |
| GPU inventory | `gpu_discovery_status=not_supported` |

**Do not treat this host as production inference hardware.**

## Lab host inventory (known)

| Role | Hostname | IPv4 | Worker id |
| --- | --- | --- | --- |
| Control plane + GPU worker | `control-plane.example.internal` | `192.0.2.10` | `worker-ai1` |
| Worker | `worker-01.example.internal` | `192.0.2.11` | `worker-ai3` |
| Worker | `worker-03.example.internal` | `192.0.2.13` | `worker-ai7` |
| Abandoned | `worker-02.example.internal` | `192.0.2.12` | — |

Measured 2026-07-31. **ai5 abandoned** — V100 PCI root ports never recovered;
replaced by **ai7** (2× V100, driver `580.126.20`, worker + verified runtime live).

## Target V100 workers (measured)

### control-plane.example.internal (`worker-ai1`, co-located with gateway)

| Field | Value |
| --- | --- |
| Hostname | `control-plane.example.internal` |
| IPv4 | `192.0.2.10` |
| Role | Gateway + Console + `worker-ai1` (separate `/opt/yonder/venv-worker`) |
| OS / kernel | RHEL 8.10 (Ootpa) / `4.18.0-553.125.1.el8_10.x86_64` |
| Architecture | x86_64 |
| CPU model | Intel Xeon Gold 6140 @ 2.30GHz |
| CPU cores | 72 |
| System memory | 187 GiB |
| Storage | `/` xfs 889G; `/srv/yonder-storage` xfs 894G (~437G used, model bundles) |
| Network | `ens9f0` UP `192.0.2.10/26` |
| GPU 0 | Tesla V100-PCIE-32GB, UUID `GPU-91130364-cf44-078d-c066-3021b4057c48`, ECC Enabled |
| GPU 1 | Tesla V100-PCIE-32GB, UUID `GPU-ff25659d-cf86-3cbb-f23a-f82d8fc93ebd`, ECC Enabled |
| NVIDIA driver | `580.126.20` (DKMS) |
| CUDA version | 13.0 (from `nvidia-smi`) |
| NVLink | not reported / not in use |
| Docker / runtime constraints | llama.cpp `b10167` CUDA `sm_70` under `/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70`; toolkit `/opt/cuda-12.6`; static escape hatch `yonder-llama-cpp.service` on loopback `:8000`; preferred path is worker-managed processes (`llama_cpp_runtime`) with gateway placement |

### worker-01.example.internal (`worker-ai3`)

| Field | Value |
| --- | --- |
| Hostname | `worker-01.example.internal` |
| IPv4 | `192.0.2.11` |
| OS / kernel | RHEL 8.10 (Ootpa) / `4.18.0-553.125.1.el8_10.x86_64` |
| Architecture | x86_64 |
| CPU model | Intel Xeon Gold 6140 @ 2.30GHz |
| CPU cores | 72 |
| System memory | 187 GiB |
| Storage | `/` xfs 889G |
| Network | `ens9f0` UP `192.0.2.11/26` |
| GPU 0 | Tesla V100-PCIE-32GB, UUID `GPU-8083819d-fddd-b69c-deae-2b6ae417aa75`, ECC Enabled |
| GPU 1 | Tesla V100-PCIE-32GB, UUID `GPU-509918fe-bfaa-5995-aa72-a51bfd525367`, ECC Enabled |
| NVIDIA driver | `580.126.20` (DKMS) |
| CUDA version | 13.0 (from `nvidia-smi`) |
| NVLink | not reported / not in use |
| Docker / runtime constraints | llama.cpp `b10167` CUDA `sm_70`; toolkit `/opt/cuda-12.6`; worker-managed `llama_cpp_runtime` + `:8090` runtime proxy; models on `/srv/yonder-storage` SSD |

### worker-03.example.internal (`worker-ai7`)

| Field | Value |
| --- | --- |
| Hostname | `worker-03.example.internal` |
| IPv4 | `192.0.2.13` |
| OS / kernel | RHEL 8.10 (Ootpa) / `4.18.0-553.125.1.el8_10.x86_64` |
| Architecture | x86_64 |
| Storage | OS `/` on `sda`; model root `/srv/yonder-storage` xfs on `sdb` (~894G) |
| GPU 0 | Tesla V100-PCIE-32GB, UUID `GPU-e7051df4-6f39-e339-7161-de2e354df0de` |
| GPU 1 | Tesla V100-PCIE-32GB, UUID `GPU-b682c4c1-b360-be01-6c8e-79b0f043de25` |
| NVIDIA driver | `580.126.20` (local-repo + prebuilt modules from ai3; `nouveau` blacklisted) |
| CUDA version | 13.0 (from `nvidia-smi`); toolkit libs `/opt/cuda-12.6` |
| Docker / runtime constraints | same verified llama.cpp `b10167` `sm_70` stack as ai3; Gateway canary PASS (`yonder-nemotron-3-nano-4b-wai7-g0` → chat `ai7-ok`) |

### worker-02.example.internal (`worker-ai5`) — ABANDONED

| Field | Value |
| --- | --- |
| Hostname | `worker-02.example.internal` |
| IPv4 | `192.0.2.12` |
| Status | Maintenance on gateway; GPU path blocked |
| GPU inventory | none — BIOS missing PCIe root ports `5d:00.0` / `ae:00.0` (IIO/IOU disabled) |
| NVIDIA driver | installed (`580.126.20`) but no devices to bind |
| Next | Re-enable CPU0/CPU1 IIO IOU ports in BIOS; re-check `lspci -d 10de:` |

## Collection commands

On the GPU server:

```bash
./scripts/inspect-host.sh --output data/v100-host-report.json
curl -s http://127.0.0.1:8090/v1/hardware | jq .
```

The inspection command emits the same validated JSON to stdout and to the optional
output path. Missing values use explicit availability states. Review the report and
copy observed values into the pending table; do not commit sensitive network details.

## Runtime selection gate

ai1, ai3, and ai7 V100 tables are measured (driver `580.126.20`, CUDA 13.0 from
`nvidia-smi`, 32GB V100s). A real gateway `llama-cpp` adapter is live with a
CUDA 12.6 toolkit compile targeting `sm_70`. CPU build retained at
`/opt/yonder/runtimes/llama.cpp-b10167-cpu` as rollback on ai1. Mock aliases remain.

Runtime packs must record the llama.cpp source revision in the runtime directory
name (currently `b10167`) and retain conversion provenance (`conversion.json`
with source path, output hash, and conversion timestamp). PyTorch, Diffusers,
and optional vLLM payloads must similarly record exact package/build versions
in their slot before operator use. Rollback is: disable/repoint Gateway aliases,
explicitly unload the deployment, restore the previous checksummed runtime slot
and manifest, then repeat the loopback canary before re-enabling traffic. Never
replace a live runtime tree in place.
