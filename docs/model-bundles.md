# Model bundle import / runtime selection runbook

## Air-gapped rule

Yonder **never** downloads model weights on RHEL hosts. Operators assemble bundles
on a connected machine, verify checksums, transfer them to the server storage
volume, then import.

## Required V100 evidence before selecting a runtime

Fill [`docs/v100-bootstrap.md`](v100-bootstrap.md) with measured values (not guesses):

* OS / kernel (`uname -r`)
* NVIDIA driver version (`nvidia-smi`)
* CUDA toolkit / compatible runtime version
* GPU name, VRAM capacity, persistence mode
* Free disk under the planned model store path
* SELinux / secure boot constraints affecting DKMS

Until that table is complete: `runtime.provider` must remain `not_selected`.

## Bundle layout

```text
vendor/model-bundles/<bundle-id>/
  bundle.yaml
  files/...
```

Schema fields (see `packages/contracts/yonder_contracts/model_bundles.py`):

* `modality` — chat | embeddings | image | video
* `aliases` — stable client aliases (never worker/GPU names)
* `capabilities` — chat, embeddings, image, video, …
* `files[]` — relative path, sha256, size_bytes, required
* `license` / `provenance`
* `hardware_constraints.status` — use `not_reported` until measured

Example incomplete manifest: `config/models/bundles/example-chat.bundle.yaml`

## Commands

```bash
# Verify a bundle directory
uv run python scripts/model-bundles.py verify /path/to/bundle

# Import into the repo vendor tree (after verify)
uv run python scripts/model-bundles.py import /path/to/bundle --dest vendor/model-bundles

# List vendor bundles
uv run python scripts/model-bundles.py list
```

## Console folder discovery

The Models page supports an automatic preparation flow:

1. Enter an allowlisted server-side model root or model folder.
2. Scan to detect safetensors / Diffusers / GGUF layouts, modality, size, and a
   runtime candidate.
3. Select **Prepare recognized models** to checksum files and write
   `bundle.yaml` beside the weights.

Set roots with `YONDER_MODEL_ROOTS` (comma-separated). The offline installer
sets this to its `--model-root` value (default `/srv/yonder/models`). Paths outside those roots
are rejected, including traversal and symlink escapes. Preparation records
`runtime.provider=not_selected` and `hardware_constraints.status=not_reported`;
it never auto-enables a model based on filenames alone.

## Offline pack staging

`make offline-pack` copies `vendor/model-bundles/` into
`payload/model-bundles/` when present. If absent, the pack reports
`model_bundles=0` / `not_bundled`.

On the control plane after install, operator-supplied bundles staged inside the
pack are imported without overwriting existing folders. A separately mounted
backplane model volume remains in place:

```bash
ls "${YONDER_MODEL_ROOTS:-/srv/yonder/models}"
# Combined pack/model disk example:
# ls /srv/yonder-storage/model-bundles
```

Use XFS or ext4 for the mounted model volume. RHEL 8 does not provide a supported
exFAT mount implementation.

Do **not** enable a Gateway deployment that points at real weights until:

1. Bundle verify returns `status=ready` with matching checksums
2. V100 evidence is recorded
3. An approved `InferenceProvider` / `EmbeddingProvider` / `GenerationProvider` adapter exists

## Fail closed

* Missing required files → `incomplete`
* Checksum mismatch → refuse import
* Runtime not selected → allowed for packaging, **not** for serving
* Never invent VRAM capacity, tokens/sec, or model readiness
