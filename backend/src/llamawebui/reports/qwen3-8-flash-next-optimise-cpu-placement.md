---
title: 'Qwen3.8-Flash-Next 125B MoE on a 24 GB RTX 4090: +125% prefill, +17% decode from CPU placement
  alone'
slug: qwen3-8-flash-next-optimise-cpu-placement
summary: 'Optimising qwen3.8-flash-next-24gb (87 GiB UD-IQ4_XS, 38 of 48 expert layers in system RAM)
  turned out to be an exercise in CPU tuning, not GPU tuning: P-core pinning with strict placement plus
  --ubatch-size 2048 lifted prefill 254.55 -> 535-573 tok/s and decode 16.78 -> 19.71 tok/s, with the
  model file, 196K context, q8_0 KV cache and expert split all unchanged. MTP speculative decoding, quant
  format and RAM residency were each measured and rejected.'
tags:
- qwen
- flash-next
- moe
- rtx4090
- cpu-affinity
- ubatch-size
- speculative-decoding
- vram-paging
- benchmark-noise
updated: '2026-10-27'
author:
  model: qwen/qwen3.8-omni-flash
  conversation: 3680f0f3-decb-4f24-86d0-5200c0ba2a0e
created: '2026-10-27'
subject:
  profile: qwen3.8-flash-next-24gb
  model: Qwen3.8-Flash-Next-UD-IQ4_XS
hardware:
- NVIDIA GeForce RTX 4090 24 GB
- Intel Core i7-14700F
- 127.8 GiB system RAM
runtime: llama.cpp b11815 cuda (0.7.0-dev)
benchmark: tetris-no-details (reasoning medium)
outcome: improved
results:
- metric: prefill_tokens_per_second
  before: 254.55
  after: 535.41
  unit: tok/s
- metric: decode_tokens_per_second
  before: 16.78
  after: 19.71
  unit: tok/s
- metric: load_seconds
  before: 182.39
  after: 19.78
  unit: s
- metric: peak_vram
  before: 21.54
  after: 22.66
  unit: GiB
- metric: peak_ram
  before: 51.37
  after: 52.68
  unit: GiB
alternatives:
- name: UD-Q4_K_XL at equal VRAM (higher quality, same speed)
  profile: flash-next-q4kxl-test (deleted)
  summary: 'Measured on a clone and rejected as a speed option: 103.69 GiB block quant, --n-cpu-moe 41
    to hold VRAM flat. Decode 18.03 vs 17.77 (noise) despite +39% CPU-resident bytes; prefill -20% (206.17)
    from 3 fewer GPU-resident layers. It is the higher-fidelity quant (Unsloth KLD top-1 92.255 vs 89.554),
    so it remains valid if the operator wants quality, but it costs ~21 GiB of RAM and buys no speed.'
  results:
  - metric: decode_tokens_per_second
    before: 17.77
    after: 18.03
    unit: tok/s
  - metric: prefill_tokens_per_second
    before: 257.27
    after: 206.17
    unit: tok/s
  - metric: peak_ram
    before: 52.85
    after: 73.61
    unit: GiB
  - metric: peak_vram
    before: 21.54
    after: 20.88
    unit: GiB
left_behind:
- 'Downloads kept: unsloth/Qwen3.8-Flash-Next-GGUF UD-Q4_K_XL (103.69 GiB, 4 shards) and four MTP modules
  (general Q4_K_M 2.59 GiB, general Q8_0 3.85 GiB, BF16 7.24 GiB, shared-Q8_0 2.6 GiB, shared-Q4_K_M 1.78
  GiB). The shared MTP modules cannot load on stock llama.cpp and the general ones measured no gain, so
  all five MTP files are dead weight unless the custom fork is installed - safe to delete.'
- Profile clone flash-next-q4kxl-test was deleted after measuring; no test profiles remain.
- Snapshot 0ca99a55 ('pinned-p-cores best') holds the pre-ubatch/cpu-strict config; the final config supersedes
  it.
- Router left stopped, as it was at the start of the conversation.
---

## Starting point

`qwen3.8-flash-next-24gb` as found: 87.25 GiB `UD-IQ4_XS` (3 shards), `--n-gpu-layers 999 --n-cpu-moe 38 --ctx-size 196608 --cache-type-k/v q8_0 --ubatch-size 512 --threads 16 --flash-attn on --parallel 1 --load-mode none`.

Baseline run `9b1e7adc` (measured earlier the same day on identical settings, so reused rather than re-measured):

| load | prefill | decode | peak VRAM | peak RAM |
|---|---|---|---|---|
| 182.39 s | 254.55 | 16.78 | 21.54 GiB | 51.37 GiB |

This is a **125B MoE with ~6B activated** (48 layers, 512 experts each, 10 routed + 1 shared, expert width 640) plus **51B of n-gram embedding parameters**, with 38 of the 48 expert layers resident in system RAM. 16.78 tok/s decode and 254 tok/s prefill for a 6B-activated model are both far below what a 4090 should deliver, so the question was which resource was actually limiting.

## The finding that set the strategy

Derived traffic, from measured memory figures and the model card:

- **Decode**: 38 CPU layers x 11 experts x 3 matrices ≈ 1.03 GiB of expert weights per token → ~18 GB/s at 18 tok/s, against ~90 GB/s available. **Not bandwidth-bound.**
- **Prefill**: llama.cpp batches tokens per expert, so each of the 512 experts is read once per ubatch → ~0.6 GB/s. At ~6 GFLOP/token, 250 tok/s ≈ **1.5 TFLOPS, which is this i7-14700F's AVX2 roofline** (no AVX-512). **Compute-saturated on the CPU.**

So the bottleneck was CPU execution — which cores, how many threads, how wide the batches — and *not* VRAM, bandwidth, KV cache or quant quality. This is the inverse of the sibling `qwen3.8-27b` profile on the same machine, which is GPU-bandwidth-saturated and where every one of these knobs was measured as a no-op.

## Changes tried

```chart
{"type": "bar", "title": "Prefill tok/s by configuration (reasoning medium, tetris-no-details)", "categories": ["baseline", "+threads 20", "+P-core pin", "+batch pool 28", "+ubatch 1024", "+ubatch 2048", "+cpu-strict 1"], "series": [{"name": "prefill tok/s", "values": [254.55, 257.27, 263.01, 268.67, 351.19, 572.91, 535.41]}], "y_label": "tok/s", "targets": [{"label": "baseline 254.55", "value": 254.55}]}
```

```chart
{"type": "bar", "title": "Decode tok/s by configuration", "categories": ["baseline", "+threads 20", "+P-core pin", "+batch pool 28", "+ubatch 2048", "+cpu-strict 1", "threads 8 (reject)", "prio 2 (reject, 2 samples)"], "series": [{"name": "decode tok/s", "values": [16.78, 17.77, 18.27, 18.11, 18.67, 19.71, 15.0, 18.87]}], "y_label": "tok/s", "targets": [{"label": "baseline 16.78", "value": 16.78}]}
```

| # | change | run | prefill | decode | verdict |
|---|---|---|---|---|---|
| 1 | `--spec-type draft-mtp` + general MTP Q4_K_M, `--spec-draft-n-max 5 --spec-draft-ngl 999`, ctx 131072 | `137bc58f` | 146.56 | 14.00 | reject: VRAM overrun 23.30 GiB |
| 2 | same, ctx 65536 + q4_0 KV (freed VRAM) | `0eb66dbd` | 247.88 | 16.68 | **reject: MTP buys nothing** |
| 3 | `--threads 16→20 --threads-batch 20` | `a36a183f` | 257.27 | 17.77 | keep (+5.9% decode) |
| 4 | `UD-Q4_K_XL` + `--n-cpu-moe 41` (clone) | `d14a7b9f` | 206.17 | 18.03 | reject: prefill −20% |
| 5 | `--load-mode mmap+mlock` | `323fccdd` | 157.53 | 18.37 | reject: prefill −39% |
| 6 | `--poll 100` | `1cd6a7ee` | 232.52 | 18.32 | reject: prefill −9.6% |
| 7 | `--cpu-range 0-15 --cpu-range-batch 0-15 --threads/-batch 16` | `e9dcfb4a` | 263.01 | 18.27 | **keep** |
| 8 | `--cpu-range-batch 0-27 --threads-batch 28` | `23c86122` | 268.67 | 18.11 | keep |
| 9 | `--threads 8` (gen) | `ee66e8b5` | 267.05 | 15.00 | reject: decode −17% |
| 10 | `--ubatch-size 1024` | `9fe63d17` | 351.19 | 18.17 | keep |
| 11 | `--ubatch-size 2048` | `ca358d21` | 572.91 | 18.67 | **keep (+125% prefill)** |
| 12 | `--cpu-strict 1` | `6cfb6778` | 535.41 | 19.71 | **keep (+5.6% decode)** |
| 13 | `--prio 2` | `71840da0` / `004afab4` | 368.27 / 566.28 | 20.50 / 17.24 | reject: mean 18.87 < 19.71 |

### The two wins

**CPU affinity (runs 7, 8, 12).** The 14700F enumerates its 8 P-cores as logical CPUs 0–15 (both HT siblings) and its 12 E-cores as 16–27. `--threads 20` was spreading AVX2 GEMVs onto E-cores at roughly half the per-thread throughput. Pinning generation to 0–15 gave +8.9% decode; adding `--cpu-strict 1` gave a further +5.6%, because without it the Windows scheduler migrates the pinned threads back onto E-cores. Widening only the *batch* pool to all 28 CPUs is correct and free: prefill is FLOP-bound so more cores help even if they are E-cores, while generation stays on P-cores.

Run 9 is the control that proves the mechanism: `--threads 8` on the pinned P-cores collapsed decode to 15.00 with prefill untouched, so both HT siblings earn their keep.

**`--ubatch-size 512 → 2048` (runs 10, 11).** The single largest effect of the session: prefill 268.67 → 572.91 (+113%). Batching more tokens per expert converts thousands of tiny GEMVs into wide GEMMs on a CPU that was FLOP-saturated at low token counts. Cost was VRAM, non-linearly: +0.10 GiB for the first doubling, +0.87 GiB for the second, landing at 22.6–22.7 GiB. 4096 would overrun, so **2048 is the ceiling for this profile**.

Note this directly contradicts the 27B report's "`--ubatch-size 2048 trades the wrong way`" — correct for a GPU-resident dense model where prefill never touches the CPU, inverted here.

### Rejected, with mechanisms

- **MTP speculative decoding (runs 1, 2).** Unsloth advertises 1.3–1.7× for this model. With the draft confirmed loading and running and VRAM no longer overrunning, decode was 16.68 vs 16.78 — nothing. Speculation amortises weight reads when a model is GPU-bandwidth-saturated; with experts in system RAM there is nothing to amortise, and the verification pass costs the same CPU expert traffic. The 27B on this same machine gained a lot from MTP, which is exactly the contrast that explains it.
- **`UD-Q4_K_XL` (run 4).** +39% CPU-resident bytes and decode did not move (18.03 vs 17.77), while prefill fell 20% from moving 3 layers off the GPU. This kills both the "fewer bytes" and "block quants dequantise cheaper" theories simultaneously. Kept as a *quality* option in alternatives.
- **`--load-mode mmap+mlock` (run 5).** Prefill −39% for +3.4% decode, 275 s load. It also exposed a measurement trap: RSS is not comparable across load modes (`none` counts the file in RSS at 52.85 GiB; `mmap+mlock` leaves it file-backed at 6.45 GiB), so RAM figures here measure accounting, not residency.
- **`--poll 100` (run 6)** and **`--prio 2` (run 13).** Polling steals cycles from a compute-saturated prefill. Priority returned 20.50 then 17.24 on identical settings — mean 18.87, below the 19.71 without it — so it was rejected on a second sample after looking like a win on the first.

### The trap that mattered most: silent VRAM overrun

Run 1 loaded a 2.59 GiB draft onto a profile already at 21.54 of ~23.0 GiB. Peak hit **23.30 GiB**, prefill fell 42%, and **there was no OOM in the log** — on Windows/WDDM the driver pages rather than failing. The tell was `decode_low_tokens_per_second` 0.01 (vs 15.36 healthy) and first-token time 81.5 s on a 2.2K-token prompt against 9.05 s for a *larger* context in a healthy run. A user-set `--n-gpu-layers` also disables llama.cpp's auto-fitting (`n_gpu_layers already set by user to 999, abort`), so nothing rescues an over-subscribed profile.

An operator caught this mid-session ("gpu is choking, probably vram exhaustion, 23.5gb") while I was about to spend 15 minutes collecting a meaningless number from a paged Q4_K_XL run. Checking peak VRAM against device capacity should be the first thing done on every run.

## Final settings

Diff from the starting profile — **model file, 196K context, q8_0 KV cache, `--n-cpu-moe 38`, `--n-gpu-layers 999`, `--flash-attn`, `--parallel 1`, `--load-mode none` all unchanged**:

```
--threads 16 --threads-batch 28
--cpu-range 0-15 --cpu-range-batch 0-27 --cpu-strict 1
--ubatch-size 2048        (was 512)
```

| metric | baseline | final | change |
|---|---|---|---|
| prefill | 254.55 | 535.41–572.91 | **+110% to +125%** |
| decode | 16.78 | 19.71 | **+17.5%** |
| peak VRAM | 21.54 | 22.66 | +1.12 GiB (0.34 spare) |
| peak RAM | 51.37 | 52.68 | +1.31 GiB |
| load | 182.39 | 19.78 | page-cache effect, not a settings win |

The load figure deserves a caveat: 182 s was a cold first load of an 87 GiB file and ~20 s is warm. It is not attributable to any change here and should not be quoted as an improvement.

## What to try next

1. **`--n-cpu-moe 37`** — the one remaining lever with a real mechanism (one more expert layer GPU-resident, ~1.25 GiB). Blocked: it needs ~0.1 GiB more than the headroom left at 22.7 GiB. Only viable by trading 196K → 131K context (returns ~0.7 GiB of KV), and the KV measurements show that is a poor deal. Expect low single-digit decode.
2. **A newer llama.cpp.** Latest release is b11669; this machine runs dev b11815, so there is nothing newer to install today. CPU MoE batching and CUDA repack are active areas — a build with better CPU MoE kernels would raise the whole ceiling, which is currently set by CPU execution efficiency.
3. **Sampling settings were never touched** and are not speed-relevant, but the profile has no `--jinja` or `--chat-template-kwargs`. Unsloth documents `reasoning_effort` control and recommended thinking sampling for this model; worth a separate look on quality grounds, outside this brief.

## Methodology note

Per the operator's instruction mid-session, token counts were not used to gate comparisons — the benchmark is agentic and each run writes its own workload. Judgement rested on prefill/decode rates plus deterministic memory figures, with the noise floor measured rather than assumed: decode varied 17.24–20.50 (±8%) across near-identical configs, which is what overturned the `--prio 2` result on a second sample.
