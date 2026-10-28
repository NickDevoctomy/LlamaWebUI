---
title: 'Qwen3.8-Flash-Next follow-up: CPU token-embedding placement did not improve the tuned RTX 4090
  profile'
slug: qwen38-flash-next-24gb-followup-placement-test
summary: Rebenchmarked the already optimized profile, then tested the community-recommended CPU placement
  for per-layer token embeddings. It freed only 0.06 GiB VRAM, reduced prefill by 6.9%, and produced an
  inconclusive 2.2% decode rise; the experiment was reverted and the profile remains at its starting settings.
tags:
- qwen
- flash-next
- rtx4090
- llama.cpp
- token-embedding
- benchmark
updated: '2026-10-27'
author:
  model: openai/gpt-6-luna
  conversation: ab703ced-ab4c-49f2-a89a-01d8217550f4
created: '2026-10-27'
subject:
  profile: qwen3.8-flash-next-24gb
  model: Qwen3.8-Flash-Next-UD-IQ4_XS
hardware:
- Intel Core i7-14700F
- NVIDIA GeForce RTX 4090 24 GB
- 127.8 GiB system RAM
runtime: b11815 cuda (0.7.0-dev)
benchmark: Tetris no details (reasoning medium)
outcome: unchanged
results:
- metric: prefill_tokens_per_second
  before: 473.0
  after: 473.0
  unit: tok/s
- metric: decode_tokens_per_second
  before: 19.13
  after: 19.13
  unit: tok/s
- metric: load_seconds
  before: 19.3
  after: 19.3
  unit: s
- metric: peak_vram
  before: 22.66
  after: 22.66
  unit: GiB
- metric: peak_ram
  before: 52.06
  after: 52.06
  unit: GiB
alternatives:
- name: CPU token-embedding placement (rejected)
  summary: Tested `--override-tensor per_layer_token_embd=CPU` on the subject profile, then reverted.
    Against the local baseline, decode rose 2.2% (within observed run variability), prefill fell 6.9%,
    peak VRAM improved by only 0.06 GiB, and peak RAM increased by 0.29 GiB. It provided too little memory
    headroom to justify risking a change to expert placement or context.
  results:
  - metric: decode_tokens_per_second
    before: 19.13
    after: 19.55
    unit: tok/s
  - metric: prefill_tokens_per_second
    before: 473.0
    after: 440.19
    unit: tok/s
  - metric: load_seconds
    before: 19.3
    after: 18.52
    unit: s
  - metric: peak_vram
    before: 22.66
    after: 22.6
    unit: GiB
  - metric: peak_ram
    before: 52.06
    after: 52.35
    unit: GiB
left_behind:
- No added profiles, downloads, or runtimes. The token-embedding override was removed by restoring the
  supplied starting snapshot; router left stopped as found.
---

## Starting point and method

The existing profile already had the CPU tuning discovered in the earlier optimisation report: 38 MoE layers on CPU, 2048 ubatch, generation pinned to logical CPUs 0–15 with strict placement, and batch work on CPUs 0–27. It also retained 196608 context and q8_0 KV cache. Earlier work had already tested alternatives including MTP, different quantization, load mode, polling, priority, thread counts, and ubatch sizes, so those were not repeated.

The community report suggested `per_layer_token_embd=CPU` as part of a separate RTX 4090 configuration, reporting about 34 tok/s on a different harness and setup. I saved that source to the research library; its results are not directly comparable to this local Tetris benchmark. The local profile was benchmarked first, then only the tensor-placement override was changed.

## Measurements

Baseline run `b7581657-d1c2-42c5-8cb4-e2585712db59`: 473.0 tok/s prefill, 19.13 tok/s decode, 19.30 s load, 22.66 GiB peak VRAM, 52.06 GiB peak RAM.

CPU token-embedding placement run `61b87661-e4eb-450c-a354-69c8c5f15929`: 440.19 tok/s prefill, 19.55 tok/s decode, 18.52 s load, 22.60 GiB peak VRAM, 52.35 GiB peak RAM. This is −6.9% prefill, +2.2% decode, −0.06 GiB VRAM and +0.29 GiB RAM. The small decode shift is not enough to distinguish from normal variability; the VRAM reduction does not meaningfully enable additional GPU-resident expert layers.

```chart
{"type":"bar","title":"Local benchmark: baseline vs CPU token-embedding placement","categories":["Baseline","Embedding on CPU"],"series":[{"name":"Prefill tok/s","values":[473.0,440.19]},{"name":"Decode tok/s","values":[19.13,19.55]}],"y_label":"tok/s"}
```

```chart
{"type":"bar","title":"Peak memory: baseline vs CPU token-embedding placement","categories":["Baseline","Embedding on CPU"],"series":[{"name":"VRAM GiB","values":[22.66,22.6]},{"name":"RAM GiB","values":[52.06,52.35]}],"y_label":"GiB"}
```

## Final state

Restored snapshot `3301030f-b49e-4364-8722-520f5ad2fb3c`. The subject profile is unchanged from the beginning of this work. The router remains stopped, matching its initial state. No additional change appeared worth testing given the prior tuning report's completed sweeps and the negligible memory headroom from this experiment.
