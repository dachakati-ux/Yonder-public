# Future A100 Expansion

Yonder must support adding 8× NVIDIA A100 80GB NVLink/NVSwitch servers without
redesigning the gateway, console, or client integrations.

## Expansion checklist

1. Install the Yonder Worker Agent on the A100 host.
2. Register or approve the worker.
3. Discover GPUs and topology (including NVLink when reported).
4. Add compatible model manifests.
5. Create deployments that reference the new worker and GPU assignments.
6. Repoint stable aliases (for example `yonder-chat`) to the new deployment.
7. Leave Yonder Chat and external clients unchanged.

## Design implications already in place

* Routing operates on a collection of deployments, not a hard-coded first GPU.
* Hardware inventory is worker-scoped and multi-GPU capable (`gpus: []`).
* Manifests declare `tensor_parallel_size` and GPU family requirements.
* Clients never see worker hostnames, GPU indexes, or filesystem paths.

## What still comes later

* Topology-aware scheduling beyond same-worker 1-GPU / 2-GPU pools
* Auto-eviction / GPU queues (explicitly out of scope for the GGUF scheduler)
* Dedicated inference / embedding / vision / batch / development worker roles
* Production vs experimental model lanes beyond alias repointing

## What is already in for V100 GGUF

* Typed `GpuPlacement` / VRAM fit / unload-conflict contracts
* Gateway admin load accepts `placement_mode` + `gpu_indexes` and refuses overlaps
* Worker llama.cpp runtime manager reports loopback `base_url` per deployment
* Console Models UI exposes per-worker GPU columns with explicit Unload CTA
* See [`gpu-model-scheduler.md`](gpu-model-scheduler.md)
