# llama.cpp CUDA sm_70 runtime staging (offline)

Copy the built runtime tree from a lab host (ai1) into this directory before
`make offline-pack`:

```text
vendor/runtimes/llama.cpp-b10167-cuda-sm70/
  bin/llama-server
  ...
```

Source of truth on ai1 today:

```text
/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70
```

Fetch into this tree (requires VPN + `ProxyJump`):

```bash
./scripts/fetch-llama-runtime-from-ai1.sh
```

Do not commit large binaries (gitignored). The pack builder stages this tree into
`payload/runtimes/` when present; `install-llama-runtime.sh` installs it under
`/opt/yonder/runtimes/`.

CUDA toolkit runfile staging: `vendor/cuda/` (see that README). Both slots are
required for a complete YON-6b offline pack.
