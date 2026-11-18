# Offline USB pack — RHEL 8.10 (air-gapped)

This pack installs Yonder **without internet** on **fresh** x86_64 RHEL 8.10 hosts
(minimal OS, no pre-installed app dependencies).

| Host | Script | What you get |
| --- | --- | --- |
| ai1 (control plane) | `install.sh` | OS RPMs + Gateway (:8080) + Console (:3000) via systemd |
| ai3 / ai5 (workers) | `install-worker.sh` | OS RPMs + Worker Agent (:8090) via systemd |
| GPU workers | `install-worker.sh --install-nvidia` | + DKMS toolchain + NVIDIA `cuda-drivers` from bundled local-repo |

Everything operators use still goes through the Gateway. Workers only register and heartbeat.

## What is bundled

* Standalone CPython, `uv`, Node 22, Python wheels, console `node_modules`/`.next`
* **`payload/system-rpms/`** — full offline dnf repo (~300 RHEL8-compatible RPMs): `rsync`,
  toolchain, `dkms` (EPEL), and OS deps needed by NVIDIA `cuda-drivers`
* **`payload/nvidia/`** — `nvidia-driver-local-repo-rhel8-*.rpm` (when present at build time)

System RPMs are fetched from **Rocky Linux 8** (+ EPEL for `dkms`), which is binary-compatible
with RHEL 8.10. If policy requires Red Hat–signed RPMs only, replace `vendor/rhel8-rpms/repo`
using `reposync` from a subscribed RHEL host / install media, keep `createrepo` metadata, and rebuild.

## Build the pack (machine WITH internet + Docker)

```bash
# 1) Drop NVIDIA local-repo RPM (optional but required for GPU hosts)
#    vendor/nvidia/*.rpm  or  ./nvidia-580.rpm

# 2) Fetch OS dependency RPMs (~225 MiB)
make fetch-system-rpms
# or: bash packaging/offline/fetch-system-rpms.sh

# 3) Build the USB tarball
make offline-pack
# → dist/yonder-offline-rhel8-amd64.tar.gz
```

## Install on RHEL 8.10 (no internet)

### Unpack

RHEL minimal includes `tar`. Unpack from the mounted deployment volume:

```bash
sudo mkdir -p /media/yonder-pack
sudo tar -xzf yonder-offline-rhel8-amd64.tar.gz -C /media/yonder-pack
cd /media/yonder-pack/yonder-offline-rhel8-amd64
```

Installers bootstrap **`payload/system-rpms`** first (so `rsync`/`curl` appear even on a bare OS),
then apply SELinux/firewall helpers and run `verify-install.sh`.

Optional: stage operator-supplied model bundles under `vendor/model-bundles/`
(see [`docs/model-bundles.md`](../docs/model-bundles.md)) before `make offline-pack`.

### Control plane (Server A)

Before installation, mount the server-native storage volume. If the disk also
contains the offline pack, keep its existing top-level layout and use the
`model-bundles` subdirectory as the model root:

```bash
# Replace the device with the model partition identified using lsblk -f.
sudo mkdir -p /srv/yonder-storage
sudo mount /dev/disk/by-uuid/MODEL_VOLUME_UUID /srv/yonder-storage
```

Use XFS or ext4. RHEL 8 does not support the exFAT filesystem currently used to
stage the drive on macOS. Direct backplane attachment does not change that
filesystem; preserve another copy of the weights before reformatting.

```bash
sudo bash install.sh \
  --model-root /srv/yonder-storage/model-bundles \
  --take-model-ownership
```

`--take-model-ownership` recursively assigns this dedicated model tree to the
`yonder` service account so the Models workflow can write `bundle.yaml`. Omit it
for an already provisioned shared volume; installation then fails closed if the
service account lacks read/write access.

Non-interactive:

```bash
sudo bash install.sh \
  --gateway-url http://control-plane.example.internal:8080 \
  --console-origin http://control-plane.example.internal:3000 \
  --model-root /srv/yonder-storage/model-bundles \
  --take-model-ownership \
  --allowlist worker-ai3,worker-ai5 \
  --non-interactive
```

### Workers

```bash
# ai3
sudo bash install-worker.sh \
  --gateway-url http://control-plane.example.internal:8080 \
  --worker-id worker-ai3 \
  --public-endpoint http://worker-01.example.internal:8090 \
  --display-name "ai3 (lab worker)" \
  --install-nvidia \
  --non-interactive

# ai5
sudo bash install-worker.sh \
  --gateway-url http://control-plane.example.internal:8080 \
  --worker-id worker-ai5 \
  --public-endpoint http://worker-02.example.internal:8090 \
  --display-name "ai5 (lab worker)" \
  --install-nvidia \
  --non-interactive
# reboot each GPU host, then: systemctl restart yonder-worker
```

`public-endpoint` must be reachable **from the Gateway on ai1** (never `0.0.0.0`).

### NVIDIA / kernel caveat

`nvidia-580.rpm` is a **local yum repository** package, not a single driver RPM. The installer:

1. Installs offline system RPMs (`dkms`, `gcc`, `kernel-devel`, …)
2. Installs the NVIDIA local-repo RPM
3. Enables `nvidia-driver:580-dkms` and installs `cuda-drivers` from that file:// repo

**DKMS requires `kernel-devel` matching `uname -r` on the GPU host.** The pack includes the
`kernel-devel` build that was current on Rocky 8.10 when RPMs were fetched. If the host kernel
differs, copy the matching `kernel-devel-$(uname -r).rpm` from RHEL media into
`payload/system-rpms/`, refresh repodata (`createrepo_c`), and re-run `install-nvidia.sh`.

### Verify

```bash
systemctl status yonder-gateway yonder-console   # on ai1
systemctl status yonder-worker                  # on ai3 / ai5
curl -fsS http://control-plane.example.internal:8080/health
nvidia-smi                                      # GPU host after reboot
```

## Layout on disk

| Path | Purpose |
| --- | --- |
| `/opt/yonder` | Runtime (venv, python, node, repo, console) |
| `/etc/yonder/*.env` | Environment files for systemd |
| `/var/lib/yonder` | SQLite DB + gateway state |
| `/var/log/yonder` | Reserved for future file logs (journald used now) |
| `/etc/yum.repos.d/yonder-offline-system.repo` | Local file:// repo for bundled system RPMs |

## Uninstall

```bash
sudo bash uninstall.sh           # keeps /var/lib/yonder
sudo bash uninstall.sh --purge-data
```

## Notes

* Lab security mode is for private networks only.
* Studio/Lab generation still returns training placeholders until a GPU runtime is wired.
* Rebuild after dependency changes: `make fetch-system-rpms && make offline-pack`
* Optional GPU inference payload: stage `vendor/cuda/*.run` and
  `vendor/runtimes/llama.cpp-b10167-cuda-sm70/`, rebuild the pack, then on the
  host run `sudo bash install-llama-runtime.sh`. See
  [`docs/gpu-model-scheduler.md`](../../docs/gpu-model-scheduler.md).
