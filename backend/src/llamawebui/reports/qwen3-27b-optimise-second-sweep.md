---
title: 'Qwen3.8 27B on RTX 4090: second optimisation sweep, and why decode is converged'
slug: qwen3-27b-optimise-second-sweep
summary: 'A second sweep of the qwen3.8-27b profile (repack, op-offload, p-split 0, threads 20, and a
  smaller UD-IQ4_XS quant) found no decode gain: decode is bandwidth-saturated at ~1.2 TB/s effective,
  so only fewer weight bytes could help, and IQ4_XS''s dequant cost absorbs its 13% byte saving. Production
  settings are unchanged from the start; IQ4_XS is recorded as a measured −1.90 GiB VRAM / −2.03 GiB RAM
  alternative.'
tags:
- qwen
- rtx4090
- decode
- speculative-decoding
- mtp
- quantization
- benchmark-noise
- converged
updated: '2026-10-26'
author:
  model: qwen/qwen3.8-omni-flash
  conversation: 8e3aa245-e52c-4c75-b13b-4f79b4c0fec2
created: '2026-10-26'
subject:
  profile: qwen3.8-27b
  model: Qwen3.8-27B-UD-Q4_K_M
hardware:
- NVIDIA GeForce RTX 4090 24 GB
- Intel Core i7-14700F
- 127.8 GiB system RAM
runtime: llama.cpp b11815 cuda (0.7.0-dev)
benchmark: tetris-no-details (reasoning medium)
outcome: unchanged
results:
- metric: decode_tokens_per_second
  before: 122.22
  after: 122.22
  unit: tok/s
- metric: prefill_tokens_per_second
  before: 2043.85
  after: 2043.85
  unit: tok/s
- metric: load_seconds
  before: 6.27
  after: 6.27
  unit: s
- metric: peak_vram
  before: 19.49
  after: 19.49
  unit: GiB
- metric: peak_ram
  before: 5.98
  after: 5.98
  unit: GiB
- metric: peak_vram (IQ4_XS alternative)
  before: 19.49
  after: 17.59
  unit: GiB
- metric: peak_ram (IQ4_XS alternative)
  before: 5.98
  after: 3.95
  unit: GiB
---

## Starting point

`qwen3.8-27b` arrived already tuned by an earlier 25-run conversation, recorded in research document
`qwen3-27b-mtp-spec-decode-rtx4090`. Its settings: all 999 layers on GPU, `--fit off`, 128K context,
`--parallel 1`, flash attention, q4_0 KV cache, `--batch-size 2048 --ubatch-size 512`, threads 16, and MTP
speculative decoding at `--spec-draft-n-max 4 --spec-draft-p-min 0.1 --spec-draft-n-min 0`.

Baseline measured fresh this session, reasoning medium:

| run | load | prefill | decode | peak VRAM | peak RAM | ctx peak | gen tokens |
|---|---|---|---|---|---|---|---|
| `0331a85b` | 6.27 s | 2043.85 | 122.22 | 19.49 | 5.98 | 13,326 | 11,816 |
| `e1591776` | 5.83 s | 2055.14 | 101.21 | 19.50 | 7.93 | 38,402 | 80,743 |

Two runs of *identical* settings differing by 21 tok/s is the central fact of this report. The benchmark is
agentic — each run writes its own workload — so the decode mean mixes speed with how much work the model
chose to do. Everything below is judged against that.

## Why I expected nothing, and tested anyway

Prefill is ~2% of wall-clock (15.4 s of 824 s in the large run), so decode was the only metric worth moving.
The arithmetic said it could not be moved: 15.33 GiB of weights at ~79 tok/s steady-state implies ~1.2 TB/s
effective read rate against a ~1.0 TB/s card. The apparent excess is what MTP buys — weights are read once
per *verification pass*, not per accepted token. No argument that leaves the byte count unchanged can beat
that ceiling.

I ran the sweep anyway, because the prior document's own recipe implied several knobs it had never measured,
and because "converged" is a claim worth testing once rather than inheriting.

## Changes tried

```chart
{"type": "bar", "title": "Decode speed by change tried (peak context in labels; lower ctx = harder to beat)", "categories": ["baseline 13.3k", "baseline 38.4k", "+repack 12.5k", "+p-split 0 11.1k", "threads 20 11.6k", "+op-offload 72.2k*", "IQ4_XS 25.4k", "IQ4_XS 38.1k"], "series": [{"name": "decode tok/s", "values": [122.22, 101.21, 121.58, 122.39, 118.48, 97.39, 123.87, 114.22]}], "y_label": "tok/s", "targets": [{"label": "baseline 122.22", "value": 122.22}]}
```

\* `+op-offload` was measured at the wrong reasoning level (see below) and is not comparable.

| change | run | decode | prefill | VRAM | verdict |
|---|---|---|---|---|---|
| `--op-offload` | `40a8b1d3` | 97.39 | 2127.82 | 19.50 | inconclusive → reverted |
| `--repack` | `6f2a36c6` | 121.58 | 2101.77 | 19.50 | no-op |
| `--spec-draft-p-split 0` | `b2c4906e` | 122.39 | 2312.68 | 19.49 | no-op |
| `--threads 20 --threads-batch 20` | `8f9b518f` | 118.48 | 2062.47 | 19.49 | no-op |
| model → `UD-IQ4_XS` (clone) | `7d2a9ded`, `595befb2` | 123.87, 114.22 | 2222.54, 2111.04 | 17.59 | memory win only |

- **`--repack`** — the strongest candidate, since CUDA weight repacking exists to improve coalescing and
  decode is bandwidth-bound. Cleanest comparison of the session (10,839 vs 11,816 generated tokens, 12.5k vs
  13.3k context): nothing moved. Its documentation targets legacy block formats (Q4_0/Q4_1/Q5/Q8_0); this
  file is a K-quant. Do not retry on K/IQ quants.
- **`--spec-draft-p-split 0`** — disables llama.cpp's default of randomly truncating drafts 10% of the time
  per step. The one genuinely untried spec knob, and the prior sweep had gone deep on `n-max`/`p-min`/`n-min`.
  Zero effect.
- **`--threads 20`** — the 14700F's physical core count, versus the recipe's 16. Within noise, slightly low.
- **`--op-offload`** — +4% prefill, which sits at the top edge of the baseline prefill band (1953–2336 over
  25 prior runs), so not causal. Reverted; it also spends VRAM headroom for nothing.

### UD-IQ4_XS, the only lever that changes the byte count

13.27 GiB vs 15.33 GiB (−13%), tested on clone profile `qwen3.8-27b-iq4xs` with settings otherwise identical,
so production was never at risk.

```chart
{"type": "bar", "title": "Memory: deterministic and reproducible (two IQ4_XS runs agreed exactly)", "categories": ["Q4_K_M (production)", "UD-IQ4_XS (clone)"], "series": [{"name": "peak VRAM GiB", "values": [19.49, 17.59]}, {"name": "peak RAM GiB", "values": [5.98, 3.95]}], "y_label": "GiB"}
```

- **Memory: real win.** −1.90 GiB VRAM, −2.03 GiB RAM, reproduced to the decimal in both runs. These are
  allocations, not timings, which is why they can be trusted when the speeds cannot.
- **Decode: unchanged.** Mean 119.05 across two samples, inside the 96–127 baseline spread. The favourable
  reading is that the 11,503-token run matched baseline 122.22 while carrying 2× the context — but that is
  one sample against a ±5–8 tok/s noise floor.
- **Load: unchanged.** I initially called this −15% from 5.31 s; the second sample came back 6.06 s, inside
  the baseline band of 5.70–6.06+ s. Corrected before it became a false recipe.
- **The predicted risk did not materialise** — IQ trellis dequant cost did not *hurt*, but it absorbed enough
  of the byte saving to leave no net gain. This is why a smaller quant failed to beat the bandwidth ceiling.

## Methodological corrections recorded

1. **`decode_final_tokens_per_second` is not a low-variance tiebreaker.** I adopted it as one mid-session and
   used it to judge three changes. Across four runs on comparable small workloads it read 79.52, 84.28,
   79.77, 89.01 — an 11.7% spread, wider than any effect it was meant to resolve. Documented so later work
   does not repeat the mistake.
2. **Reasoning level drift.** Run `40a8b1d3` recorded `reasoning_level: null` because I omitted the parameter;
   the operator caught it. Every subsequent run passed `reasoning_medium` explicitly. That run is excluded
   from comparisons rather than normalised.

## Final settings

Unchanged from the starting snapshot `44beb008`, verified argument-by-argument (18 arguments, identical).
No `restore_snapshot` was needed — the reverts were explicit. The clone is left **disabled** so the router
publishes exactly what it published before this work. Router left stopped, as found.

## What to try next

- **Do not sweep these knobs again.** Two independent sweeps and 32+ runs have found nothing. Before
  spending a run on any change, ask whether it alters the number of weight bytes read per token. If not, it
  cannot beat ~1.2 TB/s of saturation.
- **The catalog closes the remaining speculative space**: `--spec-draft-ngl`, `--spec-draft-threads`,
  `--spec-draft-threads-batch`, `--spec-draft-cache-type-k/v`, `--spec-draft-device` and
  `--spec-draft-backend-sampling` all `require --spec-draft-model`, so every one is a no-op for built-in MTP.
- **If decode must genuinely improve, the lever is outside the profile.** Either a smaller quant accepted on
  quality grounds (IQ4_XS is measured and waiting on the disabled clone), or a fixed-workload probe to
  replace this benchmark — 32 runs cannot resolve a 5% effect while each run chooses its own token counts.
- **A quality benchmark is the missing prerequisite** for switching production to IQ4_XS. Speed alone does not
  justify it, and "without breaking the model" forbids trading accuracy on measured speed that isn't there.
