# Hugging Face model discovery and verified local download

Yonder's Models page searches Hugging Face through the gateway, inspects GGUF
files, shows weight sizes, license/gated flags, and conservative estimates
against *reported* free GPU memory.

## Installing a GGUF

An operator with `models.manage` can choose **Install locally** on a public,
non-gated, top-level GGUF file. The gateway then:

1. Validates the repository identifier and filename.
2. Fetches immutable model commit metadata and a published LFS SHA-256.
3. Refuses gated/private models, unverifiable hashes or files over 40 GiB.
4. Checks available disk space with a safety margin.
5. Downloads over HTTPS from Hugging Face's allowed domains, validating
   each redirect; arbitrary user URLs and non-Hugging Face hosts are refused.
6. Shows job progress and supports cancellation.
7. Verifies exact size and SHA-256; atomically renames the finished file.
8. Produces an **incomplete**, checksum-verified Yonder bundle (runtime
   `not_selected`), ready for operator compatibility review.

**Important:** The installed file is **not automatically loaded**. Yonder
must first validate a compatible runtime and create a deployable model
definition. An incomplete bundle must not become a READY model.

The default single-host Docker Compose stack stores model downloads in the
persistent `yonder_data` volume under `/app/data/models`. The local worker
mounts the same volume read-only; remote workers do **not** receive files
automatically. Never advertise a successful install on a remote worker.

## Limits

- At most two simultaneous download jobs per gateway process.
- Job progress is process-local and resets after a gateway restart; verified
  downloaded files remain persistent.
- No automatic resume or cross-process job coordination yet.
- GGUF files in repository subdirectories, gated models, private repositories
  and files without LFS SHA-256 are not installed automatically.
- Hugging Face CDN hosts may change. Unrecognized destinations fail closed
  and require reviewed allowlist updates.
- The operator must review upstream license terms; displaying a license tag
  is **not** a legal clearance or school approval.
- Model weights are just part of memory usage. KV cache, compute buffers,
  runtime compatibility, context length and model architecture matter.
- Installed weights are not yet automatically imported into the full model
  definition and lifecycle registry; use the advanced loader for supported
  manual deployments.

## API

- `GET /api/admin/catalog/search?q=Qwen&limit=12` — `models.view`
- `GET /api/admin/catalog/models/{owner}/{repo}` — `models.view`
- `GET /api/admin/catalog/installs` — `models.view`
- `POST /api/admin/catalog/installs` — `models.manage`

  `{"repo":"owner/repository","filename":"model.Q4_K_M.gguf"}`

- `POST /api/admin/catalog/installs/{job_id}/cancel` — `models.manage`

No shell commands, remote Python scripts, automatic system dependency
installation or unsafe host paths are used by the model installer.

## Next required work for a fully automated model loader

1. Durable job records, safe resume and disk quotas with administrator-set
   limits, including multi-worker quota and concurrency coordination.
2. Worker-side verified artifact transfer, per-worker storage configuration,
   model inventory and health checks.
3. Runtime backend packaging and compatibility preflight for CPU/NVIDIA/
   AMD/Apple Silicon. Avoid claiming support without testing.
4. Convert verified bundles into typed Yonder model definitions with actual
   runtime and placement requirements, then invoke the existing load lifecycle.
5. Real inference smoke tests and measured benchmark results before showing
   a model as READY.
6. Administrator-approved catalog/terms controls for school deployments,
   role-scoped installation and audit of model-management operations.
