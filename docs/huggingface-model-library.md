# Hugging Face model discovery (first integration)

The **Models → Discover models** panel searches public Hugging Face metadata
through the Yonder Gateway. The browser never needs a Hugging Face token and
never contacts a worker directly.

## What works in this release

- Search Hugging Face models by name, using a bounded (12-result) query.
- Open model information and list GGUF weight files and their reported sizes.
- Display license, gated-access and task metadata when the upstream reports them.
- Show **tentative single-GPU weight fit** against a connected Yonder worker's
  reported *available* memory, requiring 25% extra space. This is only a
  conservative **weights-based hint**, not a deployability or speed guarantee.
- Follow a link to the upstream model card to review licensing and documentation.
- Restrict catalog access with the existing `models.view` capability.

## Important limitations

**This is discovery, not one-click installation.** It does not download model
weights, accept gated-model terms, install model runtimes, generate manifests,
start models, or modify worker storage. The existing model loader and folder
registration paths remain unchanged.

A GGUF weight's size is **not** the total VRAM required for inference. Context
length, KV cache, activation buffers, GPU/CPU splitting, architecture support
and quantization matter. A tentative fit hint must never be used to bypass
the existing Yonder placement checks.

The Hugging Face catalog requires outbound HTTPS access from the gateway.
Air-gapped installations will see a clear catalog-unavailable message and
can continue using locally installed models.

## Design for the next implementation phase

1. **Install plans:** Require a specific model revision and GGUF filename,
   inspect file hashes and sizes, model license/gated status, available disk,
   supported runtime, and worker compatibility before offering installation.
2. **Secure download jobs:** Enforce operator capability `models.manage`,
   quotas, per-worker download destination allowlists, progress tracking,
   cancellation, atomic writes, checksum validation, and cleanup after failure.
   Never execute untrusted model-repository scripts or remote code.
3. **Registration:** Generate a validated Yonder model manifest and register it
   with the existing model-discovery/lifecycle systems. Loading is a separate
   confirmed action after runtime validation.
4. **Recommendations:** Combine available worker memory and runtime support
   with license policies, benchmark evidence and measured capacity. Mark
   `estimated`, `tested`, and `unsupported` distinctly.
5. **School profiles:** Add administrator-approved model catalogs and restrict
   download, load, unload and model sharing to authorized users.

## API

- `GET /api/admin/catalog/search?q=Qwen&limit=12`
- `GET /api/admin/catalog/models/{owner}/{repo}`

Both require `models.view`. All outbound catalog requests use the fixed
`https://huggingface.co/api` upstream, do not follow redirects, and have
a bounded timeout. No user-controlled arbitrary download URL is accepted.
