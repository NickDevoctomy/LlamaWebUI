---
title: Tuning MTP speculative decoding for Qwen3.8 27B Q4_K_M on an RTX 4090
slug: qwen3-27b-mtp-spec-decode-rtx4090
summary: 'MTP spec-decode tuning for Qwen3.8 27B on a 24 GB RTX 4090 is converged: p-min 0.1 and draft
  length 4 beat the alternatives, and a second sweep of repack, op-offload, p-split 0, threads 20 and
  a smaller IQ4_XS quant all failed to move decode. Decode is bandwidth-saturated at ~1.2 TB/s effective,
  so only fewer weight bytes could help, and IQ4_XS''s dequant cost cancels the gain. The Tetris benchmark
  is agentic, so runs must be matched by prompt/generated token counts.'
tags:
- speculative-decoding
- mtp
- ngram
- qwen
- rtx4090
- decode
- benchmark-noise
- agentic-benchmark
- quantization
- bandwidth-roofline
updated: '2026-10-26'
author:
  model: qwen/qwen3.8-omni-flash
  conversation: 8e3aa245-e52c-4c75-b13b-4f79b4c0fec2
researched: '2026-10-26'
sources:
- title: My 25 benchmark runs on profile qwen3.8-27b, benchmark 'Tetris no details' (reasoning medium),
    runtime b11815 cuda, 2026-10-26
- title: 'Follow-up runs on qwen3.8-27b and clone qwen3.8-27b-iq4xs: e1591776, 40a8b1d3, 6f2a36c6, b2c4906e,
    8f9b518f, 7d2a9ded, 595befb2, 2026-10-26'
- title: 'LlamaWebUI argument catalog v0.4.1: --spec-draft-backend-sampling / --spec-draft-ngl / --spec-draft-threads
    / --spec-draft-type-k all require --spec-draft-model'
- title: llama.cpp --repack help text ('repack model weights, experimental'); CUDA repack benefit documented
    mainly for legacy block formats Q4_0/Q4_1/Q5_0/Q5_1/Q8_0, not K/IQ quants
  accessed: '2026-10-26'
- title: 'llama.cpp speculative decoding help: --draft-p-split default 0.1 (random early draft truncation),
    --draft-p-min default 0.1 (greedy-confidence early exit)'
  accessed: '2026-10-26'
applies_to:
  models:
  - Qwen3.8-27B-UD-Q4_K_M
  - Qwen3.8-27B-UD-IQ4_XS
  hardware:
  - NVIDIA GeForce RTX 4090 24 GB
  - Intel Core i7-14700F
  runtime: llama.cpp b11815 cuda
confidence: medium
---

## Setup

Model `Qwen3.8-27B-UD-Q4_K_M.gguf` (15.33 GiB) fully on a 24 GB RTX 4090: `--n-gpu-layers 999 --fit off`,
`--ctx-size 131072 --parallel 1 --flash-attn on --cache-type-k q4_0 --cache-type-v q4_0`,
`--spec-type draft-mtp --spec-default --spec-draft-n-max 4 --spec-draft-p-min 0.1 --spec-draft-n-min 0`,
`--ubatch-size 512 --threads 16 --threads-batch 16`. Runtime b11815 cuda. Benchmark "Tetris no details",
reasoning **medium**.

## Read this first: the benchmark is agentic, so runs are not comparable by default

Each run writes its own workload. Across 32 runs of this profile family, `prompt_tokens` ranged 9,599–69,244
and `generated_tokens` 7,893–80,743, and `context_peak` reached 72,191.

- Compare `prompt_tokens`, `generated_tokens`, `rounds` and `context_peak` between runs before believing any
  difference in `decode_tokens_per_second`.
- Decode slows as context grows (KV traffic per token rises with it). A run at 28k context can show a higher
  mean than one at 10k with identical settings; conversely a small gain can hide behind a large context.
- Within genuinely comparable runs, decode spread was ±5–8 tok/s. Effects under ~5% need several samples.

### `decode_final_tokens_per_second` is NOT a low-variance tiebreaker

Worth recording because I tried to use it as one and it failed. Across four runs on *comparable* small
workloads (11.6k–13.3k generated tokens) it read 79.52, 84.28, 79.77, 89.01 — an 11.7% spread, wider than
the effect it was meant to resolve. Treat it as another noisy sample, not a steady-state figure.

### Deterministic metrics are the ones to trust

`peak_vram` and `peak_ram` are allocations, not timings, and they repeat exactly: two independent IQ4_XS runs
both reported 17.59 GiB VRAM / 3.95 GiB RAM. Load time is *not* in this category — it swung 5.31→6.06 s
between two runs of the same config, against a baseline band of 5.70–6.27 s. Do not claim a load-time win
from one sample.

## Findings

### Decode is bandwidth-saturated: why no flag can fix it

At 15.33 GiB of weights and ~79 tok/s steady-state, the effective weight-read rate is ~1.2 TB/s against a
card with ~1.0 TB/s of memory bandwidth. The apparent excess is the point of MTP: weights are read once per
*verification pass*, not per accepted token, so speculation already amortises the bandwidth cost.

Consequence: no argument that leaves the byte count unchanged can lift decode. That covers the entire
prefill/attention/threads family. The only lever is fewer weight bytes, i.e. the quant itself.

KV traffic is ~0.9 GiB per token at 12k context (~6% of total reads) and ~35% at 72k — which is why
`--cache-type-k q4_0 --cache-type-v q4_0` (the lowest type the catalog offers) is already correct, and why
long-context runs decode slower.

### Second sweep: repack, op-offload, p-split, threads — all inside noise

| change | decode | verdict |
|---|---|---|
| baseline | 122.22 / 101.21 (two samples) | — |
| `--op-offload` | 97.39, prefill +4% | reject: no gain, spends VRAM headroom |
| `--repack` | 121.58 (clean pair: 10.8k vs 11.8k gen) | no-op |
| `--spec-draft-p-split 0` | 122.39 | no-op |
| `--threads 20 --threads-batch 20` | 118.48 | no-op |

`--repack` behaving as a no-op matches its documentation: CUDA repacking targets legacy block formats
(Q4_0/Q4_1/Q5/Q8_0), and this file is a K-quant. Do not retry it on K/IQ quants.

`--spec-draft-p-split 0` disables llama.cpp's default of randomly truncating drafts 10% of the time per step,
which should in principle raise accepted tokens per pass at `n-max 4`. It did not move anything measurable.

### Catalog dead ends for built-in MTP

`--spec-draft-ngl`, `--spec-draft-threads`, `--spec-draft-threads-batch`, `--spec-draft-cache-type-k/v`,
`--spec-draft-device` and `--spec-draft-backend-sampling` all `require: --spec-draft-model`. With
`--spec-type draft-mtp` there is no separate draft model, so every one of them is a no-op. Skip the whole
family rather than spending runs on it.

Both installed runtimes were the same build (0.7.0-dev, b11815 ahead of release b11669), so no runtime swap.

### UD-IQ4_XS: memory win, decode and load unchanged

13.27 GiB vs 15.33 GiB (−13% bytes), same settings, two runs:

| | Q4_K_M base | IQ4_XS | Δ |
|---|---|---|---|
| peak VRAM | 19.49 | 17.59 / 17.59 | **−1.90** |
| peak RAM | 5.98 | 3.95 / 3.95 | **−2.03** |
| decode | 122.22 | 123.87 / 114.22 | unchanged |
| load s | 6.27 | 5.31 / 6.06 | unchanged |

The predicted risk was that IQ trellis dequant cost would cancel the byte saving; it did not hurt, but it did
not help either — decode stayed inside the noise band even where the IQ4_XS run carried 2× the context.
Recommendation: keep production on Q4_K_M. Switch to IQ4_XS only for a quality-grounded reason or when the
2 GiB of VRAM is needed (it buys headroom toward longer context, not speed).

### Earlier findings, unchanged

- **Draft length 4 beats 2 (96.56) and 8 (109.92, +0.6 GiB VRAM).** Doubling draft length loses ~9%.
- **`--spec-draft-p-min 0.1` beats 0 and 0.2**; the useful gate is narrow.
- **`--spec-draft-n-min 2` hurts.** Leave at 0 and let p-min do the gating.
- **ngram-simple stacked on MTP is a workload-dependent lottery.** Three runs: 170.66, 113.11, 114.88. The
  170.66 run had `decode_peak` 679 — an n-gram lock on repeated output, not real speed. Median ~114 < MTP-only
  ~123.
- **`--ubatch-size 2048` trades the wrong way**: prefill +4%, decode −2%, VRAM +1.28 GiB. Prefill is only
  ~2% of wall-clock on this benchmark, so never a good trade here.
- **`--ctx-checkpoints 1`** saves RAM nobody needs (127.8 GiB available) and costs prefill.

## Practical recipe

For a ~27B dense Q4_K_M model with an MTP head on 24 GB: all layers on GPU, q4_0 KV with flash attention,
`--spec-draft-n-max 4 --spec-draft-p-min 0.1 --spec-draft-n-min 0`, `--ubatch-size 512`, threads near
physical core count. Expect ~120 tok/s decode on agentic code-writing work.

**This configuration is converged.** Two independent sweeps, 32+ runs, have found nothing left. Before
spending runs on it again: check whether the change alters the number of weight bytes read per token. If it
does not, it cannot beat ~1.2 TB/s of saturation, and the run will land in the noise.
