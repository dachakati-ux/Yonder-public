# CUDA toolkit staging (offline)

Stage NVIDIA **CUDA toolkit** runfiles here for air-gapped host installs.
Do **not** commit the large `.run` / `.rpm` blobs (gitignored).

## Lab install record (ai1, 2026-07-30)

| Item | Value |
| --- | --- |
| Toolkit | CUDA **12.6.3** (toolkit-only) |
| Installer | `cuda_12.6.3_560.35.05_linux.run` |
| MD5 | `29d297908c72b810c9ceaa5177142abd` |
| Host path | `/opt/cuda-12.6` (`nvcc` 12.6.85) |
| Driver | left unchanged at `580.126.20` |
| Why 12.6 | V100 = `sm_70`; CUDA **13** toolkits dropped Volta compile support |

Install pattern used:

```bash
sudo ./cuda_12.6.3_560.35.05_linux.run --silent --toolkit --toolkitpath=/opt/cuda-12.6
sudo chmod -R a+rX /opt/cuda-12.6
```

This toolkit is for compiling/running llama.cpp CUDA builds. It is **not** a
driver install and was not used to alter host firewall or NVIDIA driver packages.
