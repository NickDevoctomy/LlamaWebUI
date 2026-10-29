---
title: 'Qwen3.8-27B MTP draft-depth comparison: 2/3/4/5 tokens, MTP off, and p-min test'
slug: qwen38-27b-mtp-draft-depth-comparison-2026-10
summary: Three-run averages on Tetris-medium compare MTP maxima 2/3/4, MTP off, and draft-5 at p-min 0.1
  and 0.2. Draft-5 averaged 120.85 tok/s decode over three valid runs at p-min 0.1 versus 124.36 tok/s
  at p-min 0.2; that 2.9% difference is suggestive but inconclusive on a variable agentic workload.
tags:
- qwen3.8
- mtp
- speculative-decoding
- draft-depth
- rtx4090
- benchmark
updated: '2026-10-29'
author:
  model: openai/gpt-6-luna
  conversation: 82e4a98f-b544-485e-aa74-21643ba1b50a
created: '2026-10-29'
subject:
  profile: qwen3.8-27b
  model: Qwen3.8-27B-UD-Q4_K_M
hardware:
- Intel Core i7-14700F
- NVIDIA GeForce RTX 4090 24 GB
- 127.8 GiB system RAM
runtime: b11815 cuda debug (0.7.0-dev, commit 7170cad)
benchmark: Tetris no details, reasoning medium
outcome: unchanged
results:
- metric: MTP-4 p-min 0.1 mean decode
  before: 110.34
  after: 117.87
  unit: tok/s
- metric: MTP-4 p-min 0.1 mean prefill
  before: 1939.53
  after: 2216.87
  unit: tok/s
- metric: MTP-5 p-min 0.1 mean decode
  after: 120.85
  unit: tok/s
- metric: MTP-5 p-min 0.1 mean prefill
  after: 2091.34
  unit: tok/s
- metric: MTP-5 p-min 0.2 mean decode
  after: 124.36
  unit: tok/s
- metric: MTP-5 p-min 0.2 mean prefill
  after: 2138.99
  unit: tok/s
alternatives:
- name: MTP draft max 2, p-min 0.1 (3-run mean)
  profile: q38-mtp2-test (disabled)
  summary: Mean 91.10 tok/s decode and 2,238.47 tok/s prefill; extra fourth run excluded.
  results:
  - metric: decode
    after: 91.1
    unit: tok/s
  - metric: prefill
    after: 2238.47
    unit: tok/s
  - metric: load
    after: 5.83
    unit: s
  - metric: peak VRAM
    after: 19.2
    unit: GiB
  - metric: peak RAM
    after: 6.1
    unit: GiB
- name: MTP draft max 3, p-min 0.1 (3-run mean)
  profile: q38-mtp3-test (disabled)
  summary: Three-run mean; same base settings except draft maximum.
  results:
  - metric: decode
    after: 109.95
    unit: tok/s
  - metric: prefill
    after: 2122.87
    unit: tok/s
  - metric: load
    after: 5.83
    unit: s
  - metric: peak VRAM
    after: 19.34
    unit: GiB
  - metric: peak RAM
    after: 6.12
    unit: GiB
- name: MTP off (3-run mean)
  profile: q38-mtp-off-test (disabled)
  summary: Three-run mean with speculative decoding arguments removed.
  results:
  - metric: decode
    after: 48.17
    unit: tok/s
  - metric: prefill
    after: 2540.34
    unit: tok/s
  - metric: load
    after: 5.69
    unit: s
  - metric: peak VRAM
    after: 17.86
    unit: GiB
  - metric: peak RAM
    after: 5.47
    unit: GiB
- name: MTP draft max 5, p-min 0.1 (3 valid runs)
  profile: q38-mtp5-test (disabled, p-min 0.1)
  summary: 'Three valid runs: `0812fa77`, `b89590d7`, `b4324820`. The canceled failed run is excluded.'
  results:
  - metric: decode
    after: 120.85
    unit: tok/s
  - metric: prefill
    after: 2091.34
    unit: tok/s
  - metric: load
    after: 5.75
    unit: s
  - metric: peak VRAM
    after: 19.64
    unit: GiB
  - metric: peak RAM
    after: 6.14
    unit: GiB
- name: MTP draft max 5, p-min 0.2 (3-run mean)
  profile: q38-mtp5-pmin02-test (disabled)
  summary: 'Three completed runs: `32d07990`, `f62b2501`, `d65e752b`.'
  results:
  - metric: decode
    after: 124.36
    unit: tok/s
  - metric: prefill
    after: 2138.99
    unit: tok/s
  - metric: load
    after: 6.01
    unit: s
  - metric: peak VRAM
    after: 19.64
    unit: GiB
  - metric: peak RAM
    after: 6.18
    unit: GiB
- name: MTP draft maxima 6 and 7, p-min 0.1 (one exploratory run each)
  profile: q38-mtp6-test and q38-mtp7-test (disabled)
  summary: 'Draft 6: 120.00 decode / 1,915.18 prefill tok/s, 19.78 GiB VRAM. Draft 7: 108.22 decode /
    2,115.84 prefill tok/s, 19.93 GiB VRAM. Single samples only.'
  results:
  - metric: draft 6 decode
    after: 120.0
    unit: tok/s
  - metric: draft 7 decode
    after: 108.22
    unit: tok/s
left_behind:
- 'Disabled test clones kept: q38-mtp2-test, q38-mtp3-test, q38-mtp4-test, q38-mtp-off-test, q38-mtp4-pmin02-test,
  q38-mtp5-test (p-min 0.1, disabled), q38-mtp5-pmin02-test, q38-mtp6-test, q38-mtp7-test. No new model
  downloads or runtimes.'
- Original qwen3.8-27b profile unchanged. Router left stopped as found.
---

## Scope and method

Subject: Qwen3.8-27B-UD-Q4_K_M on RTX 4090, runtime b11815 CUDA debug (0.7.0-dev, commit 7170cad). Every measurement uses **Tetris no details, reasoning medium**. The original profile was not changed. The four requested configurations (MTP maxima 2, 3, 4 at p-min 0.1, plus MTP off) each have three completed samples and arithmetic means. The Tetris workload is agentic; means characterize sampled runs, not fixed-workload throughput guarantees.

Operator-directed exclusions: initial draft-4 sample `903fa98b` is excluded; canceled draft-4 run `b6cc330e` failed after context exhaustion. Draft-2's extra fourth completed run `14d664d5` is excluded from its requested three-run mean. Draft-5 at p-min 0.1 initially had a canceled third run `ad67769a` (failed with completion stream ReadError); after the operator reminder that the profile was changed but not measured, a valid third sample `b4324820` was run at p-min 0.1 and completes the three-run group.

## Three-run requested comparison

| Configuration | Decode mean (tok/s) | Prefill mean (tok/s) | Load mean (s) | VRAM mean (GiB) | RAM mean (GiB) | Runs |
|---|---:|---:|---:|---:|---:|---|
| MTP max 2, p-min 0.1 | 91.10 | 2,238.47 | 5.83 | 19.20 | 6.10 | `606f90b7`, `efdec04e`, `f9a90340` |
| MTP max 3, p-min 0.1 | 109.95 | 2,122.87 | 5.83 | 19.34 | 6.12 | `82759617`, `00689e0a`, `7eef571c` |
| MTP max 4, p-min 0.1 | 117.87 | 2,216.87 | 5.79 | 19.49 | 6.13 | `98c2922f`, `1a535163`, `79803d09` |
| MTP off | 48.17 | 2,540.34 | 5.69 | 17.86 | 5.47 | `2f98c718`, `580c3110`, `3120411f` |

Draft 4 was the fastest mean decode among those cases. Versus MTP off, decode was about 2.45× higher, with about 1.63 GiB additional VRAM and 0.66 GiB RAM; MTP-off prefill was higher. Production already uses draft 4 / p-min 0.1, and remains unchanged.

```chart
{"type":"bar","title":"Three-run mean decode for requested settings","categories":["MTP off","MTP max 2","MTP max 3","MTP max 4"],"series":[{"name":"decode tok/s","values":[48.17,91.10,109.95,117.87]}],"y_label":"tokens/s"}
```

## Draft-5 threshold comparison

The operator asked to measure draft 5 more fully, then try a slightly higher `--spec-draft-p-min`. At draft maximum 5, three completed samples are now available at each threshold:

| p-min | n | Mean decode | Mean prefill | Mean load | Mean VRAM | Mean RAM | Runs |
|---:|---:|---:|---:|---:|---:|---:|---|
| 0.1 | 3 | 120.85 tok/s | 2,091.34 tok/s | 5.75 s | 19.64 GiB | 6.14 GiB | `0812fa77`, `b89590d7`, `b4324820` |
| 0.2 | 3 | 124.36 tok/s | 2,138.99 tok/s | 6.01 s | 19.64 GiB | 6.18 GiB | `32d07990`, `f62b2501`, `d65e752b` |

At p-min 0.2, draft-5's decode mean is 3.51 tok/s (2.9%) higher than at 0.1, with 47.65 tok/s more prefill and effectively identical mean VRAM; RAM is 0.04 GiB higher. The decode difference is modest and Tetris workloads vary, so this is suggestive rather than a proven causal threshold gain or a demonstration of stability. There is no reason to change the production profile based on it. The safer recommendation remains the well-tested draft-4 / p-min 0.1 setup; if the operator prioritizes experimenting with draft 5, p-min 0.2 is the better observed combination, not yet a conclusively better production setting.

```chart
{"type":"bar","title":"Draft-5 mean speeds by p-min (three runs each)","categories":["p-min 0.1","p-min 0.2"],"series":[{"name":"decode tok/s","values":[120.85,124.36]},{"name":"prefill tok/s","values":[2091.34,2138.99]}],"y_label":"tokens/s"}
```

A single draft-4 p-min 0.2 probe (`931d23cb`) returned 122.17 tok/s decode and 1,995.77 tok/s prefill; it is not a repeat group and is not used to generalize threshold effects.

## Other depth probes

One exploratory run each at p-min 0.1: draft 5 first sample `0812fa77` = 120.06 decode / 1,914.26 prefill tok/s; draft 6 `d766c375` = 120.00 / 1,915.18; draft 7 `b2c9d8c9` = 108.22 / 2,115.84. VRAM was 19.66, 19.78, 19.93 GiB respectively. These single samples do not establish rankings, but drafts 6–7 show no reason to increase depth beyond 4–5. Draft 5 has now been tested in matched three-run groups at p-min 0.1 and 0.2 above.

## Final state

Every completed run was evaluated. Production profile `qwen3.8-27b` is unchanged. Test clones are disabled and the router is stopped as found. `q38-mtp5-test` is returned to p-min 0.1 and disabled; its latest valid run measured that setting. No new models or runtimes were downloaded.
