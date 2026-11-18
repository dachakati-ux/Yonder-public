# Conversion tools (offline)

Pinned linux/cp312 wheels for `convert_hf_to_gguf.py` on air-gapped V100 hosts.

## Contents

* `wheels/` — torch 2.11.0+cpu, transformers 4.57.6, numpy, gguf, sentencepiece,
  protobuf, and their direct dependencies. Verified install path on ai1:

```bash
python3.12 -m venv /opt/yonder/yonder-convert-venv
/opt/yonder/yonder-convert-venv/bin/python -m pip install --no-index \
  --find-links vendor/conversion-tools/wheels \
  torch numpy sentencepiece transformers gguf protobuf safetensors
```

## Notes

* ai1 has no PyPI egress; do not expect `pip install` from the public index.
* `convert_hf_to_gguf.py` emits only f32/f16/bf16/q8_0/tq*; K-quants need
  `llama-quantize`, which the current sm_70 runtime tree does not ship.
