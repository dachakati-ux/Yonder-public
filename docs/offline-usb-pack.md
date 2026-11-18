# Offline deployment pack (RHEL 8.10 air-gapped)

Build on a connected machine (Docker required), transfer to the backplane
storage disk only after validation, and install on **fresh** RHEL 8.10 servers
with **no internet and no pre-installed dependencies**.

Lab hosts:

| Role | Hostname | IPv4 | Worker id |
| --- | --- | --- | --- |
| Control plane | `control-plane.example.internal` | `192.0.2.10` | — |
| Worker | `worker-01.example.internal` | `192.0.2.11` | `worker-ai3` |
| Worker | `worker-02.example.internal` | `192.0.2.12` | `worker-ai5` |

```bash
# Optional: drop NVIDIA local-repo RPM first
#   vendor/nvidia/*.rpm  or  ./nvidia-580.rpm

make fetch-system-rpms   # ~300 Rocky/EPEL RPMs → vendor/rhel8-rpms/repo
make offline-pack
# → dist/yonder-offline-rhel8-amd64.tar.gz
```

On **ai1** (control plane):

```bash
sudo mkdir -p /srv/yonder-storage
sudo mount /dev/disk/by-uuid/MODEL_VOLUME_UUID /srv/yonder-storage
tar -xzf yonder-offline-rhel8-amd64.tar.gz
cd yonder-offline-rhel8-amd64
sudo bash install.sh \
  --gateway-url http://control-plane.example.internal:8080 \
  --console-origin http://control-plane.example.internal:3000 \
  --model-root /srv/yonder-storage/model-bundles \
  --take-model-ownership \
  --allowlist worker-ai3,worker-ai5 \
  --non-interactive
```

The model volume must use XFS or ext4. The staging drive's current exFAT
filesystem is not supported by RHEL 8 natively. For a one-time offline mount
of legacy exFAT media (then copy onto XFS), use the small helper pack:

```bash
# On a machine with internet + Docker (e.g. Mac):
make fetch-exfat-rpms
# Bundle is also at dist/yonder-exfat-el8-rpms.tar.gz after packaging below.

# From a jump-host-capable laptop (use your account, not root; absolute paths):
scp dist/yonder-exfat-el8-rpms.tar.gz ai1:/opt/yonder/
ssh ai1
cd /opt/yonder && tar -xzf yonder-exfat-el8-rpms.tar.gz
# Run as a SINGLE line — pasted line-continuations break arg parsing on older bundles:
sudo bash /opt/yonder/yonder-exfat-bundle/scripts/enable-exfat.sh --rpm-dir /opt/yonder/yonder-exfat-bundle/exfat-rpms/repo --device /dev/sdc2 --mountpoint /mnt/yonder-src
```

Full operator runbook: [`ai1-exfat-migration-handoff.md`](ai1-exfat-migration-handoff.md).

> Lab install helpers may reference firewalld/SELinux scripts. Those are
> **lab only — pending security review**. On YAKL-managed hosts, do not use them
> to change managed firewall policy; file a change request instead.
Then format the blank/XFS target disk, rsync off exFAT onto
`/srv/yonder-storage`, and run `install.sh` with
`--model-root /srv/yonder-storage/model-bundles`. Do not keep production
model roots on exFAT.

On **ai3** and **ai5**:

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
# reboot, then: systemctl restart yonder-worker
```

Or drivers only: `sudo bash install-nvidia.sh`

The pack includes `payload/system-rpms/` so installers can bootstrap `rsync`/`curl`, DKMS
toolchain, and NVIDIA OS dependencies without RHEL subscription mirrors.

Also included when present: `payload/model-bundles/` (operator-supplied; see
[`model-bundles.md`](model-bundles.md)). Install helpers: `verify-install.sh`,
`configure-firewall.sh`, `configure-selinux.sh`.

### Runtime payload slots and verification

Every pack now has checksummed slots, even when an optional payload is absent:

* `payload/runtimes/llama.cpp-b10167-cuda-sm70/` — measured sm_70 llama.cpp;
* `payload/conversion-tools/` — pinned conversion source/tools;
* `payload/vllm-sm70/` — optional, host-canary-gated vLLM build;
* `payload/diffusers-wheels/` — optional PyTorch/Diffusers wheel set;
* `payload/systemd/` — llama.cpp and generation runtime units.

Optional slots contain `STATUS.txt` with `status=not_reported` until an operator
stages reviewed artifacts under the matching `vendor/` directory. Absence is not
treated as readiness. `MANIFEST.sha256` covers every staged file.

Run `sudo bash verify-runtime-artifacts.sh` after installation. It fails closed
on missing checksums/runtime evidence, unresolved CUDA libraries, absent import
versions, non-loopback runtime units, missing model-root confinement, or unsafe
permissions. A missing optional runtime is reported as `not_reported`, never as
available.

The ai1 live lane remains the source of measured compatibility evidence:
toolkit `/opt/cuda-12.6` (12.6.3, toolkit-only) and runtime
`/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70`. Copying either into a pack
does not promote a model; conversion, canary, and explicit alias rollout remain
separate gates.

Full details: [`packaging/offline/README.md`](../packaging/offline/README.md) and
[`multi-host-deployment.md`](multi-host-deployment.md).
