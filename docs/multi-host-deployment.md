# Multi-host deployment (ai1 control plane + ai3/ai7 workers)

Lab rack inventory:

```text
control-plane.example.internal  192.0.2.10  — control plane
  Yonder Gateway (:8080)
  Yonder Console (:3000)
  (optional Postgres)

worker-01.example.internal  192.0.2.11  — worker-ai3
  Yonder Worker Agent (:8090)

worker-03.example.internal  192.0.2.13  — worker-ai7
  Yonder Worker Agent (:8090)

worker-02.example.internal  192.0.2.12  — abandoned
  (GPU PCI root ports never recovered; replaced by ai7)
```

Everything operators use (Chat, Studio, Lab, Hardware, Operations) talks **only** to
the Gateway on **ai1**. Workers never expose inference directly to browsers.

## Prerequisites

* Docker on each host (or process-local `make gateway` / `make worker` / `make console`)
* Private network routes: workers → `http://control-plane.example.internal:8080`
* Gateway → each worker public endpoint (`http://ai3…:8090`, `http://ai7…:8090`)
* Firewall: allow 8080/3000 on ai1 from operators; allow 8090 on ai3/ai7 from ai1 only

Managed inference processes never bind to the worker's public interface.
Gateway requests use the fixed worker endpoint:

```text
client → ai1 Gateway :8080
       → ai3 Worker :8090/v1/runtime/<deployment-id>/...
       → ai3 llama.cpp 127.0.0.1:8100+
```

Do not expose the dynamic 8100+ range. The worker proxy only forwards the
allowlisted llama.cpp health, discovery, chat, embedding, and rerank routes for
an active deployment.

## Storage layout (ai1 + ai3 + ai7)

Model manifests use absolute paths under `/srv/yonder-storage/model-bundles`, so
that mountpoint is the served-model root on every GPU host.

| Host | Disks | Layout | Mount | Role |
|------|-------|--------|-------|------|
| ai1 | 4× 1.2 TB Intel SSD | mdadm RAID10 (~2.4 TB, redundant) | `/srv/yonder-storage` | Authoritative served-model root (NFSv4 export, read-only to lab) |
| ai1 | 4× 16 TB Seagate HDD | mdadm RAID6 (~32 TB, 2-disk fault tolerance) | `/srv/yonder-archive` | Original safetensors, derived GGUF, offline pack, artifact history, backups |
| ai1 | 894 GB Micron SSD (`sdc`) | existing xfs | `/srv/yonder-storage.old` | Previous catalog, preserved as rollback after migration |
| ai3 / ai7 | local SSD kept aside | xfs | `/srv/yonder-storage.local` | Optional local scratch / former local copies after NFS cutover |
| ai3 / ai7 | NFS client | NFSv4 from ai1 | `/srv/yonder-storage` | Shared read-only catalog (same manifest paths as ai1) |

### Shared model root (NFS)

Lab hosts share a 25Gb fabric. Prefer one catalog on ai1 over rsyncing every
GGUF to every worker:

```bash
# On ai1 — export the SSD catalog read-only to 192.0.2.0/24
sudo /opt/yonder/src/scripts/provision-ai1-nfs-export.sh

# On each worker — move local volume aside and mount NFS at the same path
sudo CONFIRM=1 /opt/yonder/src/scripts/provision-worker-nfs-client.sh
```

Workers keep their former local XFS at `/srv/yonder-storage.local` for scratch.
If NFS load times grow too large, pin hot models locally again and compare
`duration_ms` in deployment logs / `yonder_model_load_duration_seconds`.

Provision local disks (still useful for scratch / fallback) with:

```bash
# ai1: build both RAID tiers and migrate the 460G catalog onto the SSD array.
# ALLOW_WIPE=1 clears leftover DOS/partition labels on otherwise empty disks.
sudo ALLOW_WIPE=1 /opt/yonder/src/scripts/provision-ai1-storage.sh

# ai3: format the new SSD as the local model root, then install runtime + models
sudo /opt/yonder/src/scripts/provision-ai3-storage.sh
sudo /opt/yonder/src/scripts/deploy-worker-verified-runtime.sh \
  /opt/yonder/src /opt/yonder/yonder-ai3-stage

# ai7: spare SSD by stable by-id serial, then same verified stage as ai3
sudo ALLOW_WIPE=1 /opt/yonder/src/scripts/provision-ai7-storage.sh
# rsync stage from ai3 (or rebuild), then:
sudo /opt/yonder/src/scripts/deploy-worker-verified-runtime.sh \
  /opt/yonder/src /opt/yonder/yonder-ai7-stage
```

`provision-ai1-storage.sh` copies the existing catalog to the new SSD array and
leaves the original `sdc` data mounted read-only at `/srv/yonder-storage.old` as
a rollback; it never erases the old volume. RAID arrays are usable immediately
while they resync in the background (`cat /proc/mdstat`).

## 1. Control plane (ai1)

```bash
cp .env.control-plane.example .env.control-plane
# Confirm:
#   NEXT_PUBLIC_YONDER_GATEWAY_URL=http://control-plane.example.internal:8080
#   YONDER_CONSOLE_ORIGIN=http://control-plane.example.internal:3000
#   allowlist: worker-ai3,worker-ai7

make compose-control-plane
# or:
# docker compose -f compose.control-plane.yaml --env-file .env.control-plane up --build
```

Console image bakes `NEXT_PUBLIC_YONDER_GATEWAY_URL` at **build** time — rebuild after
changing the Gateway URL operators will open in a browser.

`config/environments/lab-multinode.yaml` already includes
`http://control-plane.example.internal:3000` and the ai1 IP in `cors_origins`.

## 2. Workers (ai3 and ai7)

On each worker host:

```bash
cp .env.worker.example .env.worker
# ai3:
#   YONDER_WORKER_ID=worker-ai3
#   YONDER_WORKER_PUBLIC_ENDPOINT=http://worker-01.example.internal:8090
# ai7:
#   YONDER_WORKER_ID=worker-ai7
#   YONDER_WORKER_PUBLIC_ENDPOINT=http://worker-03.example.internal:8090
# Both:
#   YONDER_GATEWAY_URL=http://control-plane.example.internal:8080

make compose-worker
# or:
# docker compose -f compose.worker.yaml --env-file .env.worker up --build
```

`YONDER_WORKER_PUBLIC_ENDPOINT` is required for multi-host. Lifecycle callbacks from
the Gateway use this URL. Binding still uses `0.0.0.0` inside the container.

## 3. Offline install commands (RHEL)

On **ai1**:

```bash
sudo bash install.sh \
  --gateway-url http://control-plane.example.internal:8080 \
  --console-origin http://control-plane.example.internal:3000 \
  --model-root /srv/yonder-storage/model-bundles \
  --take-model-ownership \
  --allowlist worker-ai3,worker-ai7 \
  --non-interactive
```

On **ai3**:

```bash
sudo bash install-worker.sh \
  --gateway-url http://control-plane.example.internal:8080 \
  --worker-id worker-ai3 \
  --public-endpoint http://worker-01.example.internal:8090 \
  --display-name "ai3 (lab worker)" \
  --install-nvidia \
  --non-interactive
```

On **ai7**:

```bash
sudo bash install-worker.sh \
  --gateway-url http://control-plane.example.internal:8080 \
  --worker-id worker-ai7 \
  --public-endpoint http://worker-03.example.internal:8090 \
  --display-name "ai7 (lab worker)" \
  --install-nvidia \
  --non-interactive
```

## 4. Verify

1. Gateway health: `curl http://control-plane.example.internal:8080/health`
2. Console: open `http://control-plane.example.internal:3000`
3. Hardware page: both workers appear with heartbeats
4. Operations → workers list shows `worker-ai3` and `worker-ai7`
5. Chat: alias `yonder-chat` → llama.cpp CUDA `sm_70` on ai1 V100s. Studio / Lab
   generation still returns **training placeholders** until a generation
   provider is wired.

## Node notes

Example overlays (documentation / copy-paste) live under `config/nodes/`:

* `control-plane.example.yaml` (ai1)
* `worker-ai3.example.yaml`
* `worker-ai7.example.yaml`

Today the loader applies **defaults → environment YAML → env vars**. Node YAML
examples are not auto-merged; use `.env.*` for per-host values.

## Security notes

* `lab-multinode.yaml` uses development security for bring-up (**lab only —
  pending corporate auth review/revision**). It supplies a non-production mock
  principal. Do **not** expose that mode on a public network.
* Production must use `trusted_ingress`, worker allowlist, and upstream identity headers.
* Do not invent GPU metrics for demos — Hardware shows explicit availability states.

## Runtime rollout status / remaining work

* ai1 has the measured CUDA 12.6 + llama.cpp `sm_70` runtime and verified model
  catalog. ai3 uses the same pinned artifacts and manifest-identical model paths
  when enabled with `scripts/deploy-worker-verified-runtime.sh`.
* ai7: PCI shows 2× V100; NVIDIA driver + worker + model volume still required.
* Real image/video diffusion (Studio path is ready; provider adapter is not)
* NVIDIA container runtime / CUDA image variants for GPU workers (optional path)

## Offline pack and backplane model disk (no internet on RHEL)

Build once on a connected machine and transfer the pack after validation. On the
control plane (**ai1**), mount the XFS/ext4 model volume at `/srv/yonder/models`
before running `install.sh` (or mount a combined pack/model disk at
`/srv/yonder-storage` and pass its `model-bundles` directory with
`--model-root`); direct backplane attachment does not make exFAT supported on
RHEL 8. Run `install-worker.sh` on **ai3** and **ai7**. See
[`offline-usb-pack.md`](offline-usb-pack.md).
