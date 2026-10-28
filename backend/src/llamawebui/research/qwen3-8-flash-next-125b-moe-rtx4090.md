---
title: 'Qwen3.8-Flash-Next 125B MoE on a 24 GB RTX 4090: CPU placement-bound, and how that inverts the
  dense-model recipe'
slug: qwen3-8-flash-next-125b-moe-rtx4090
summary: 'Qwen3.8-Flash-Next (125B MoE, 6B active, hybrid Gated DeltaNet + QSA + 51B n-gram embedding)
  with most experts in system RAM is the inverse of a GPU-resident dense model on a 24 GB RTX 4090: --ubatch-size
  is the dominant prefill lever (+125%), P-core pinning with --cpu-strict is the decode lever (+17%),
  --threads matters, and MTP speculative decoding gains nothing. Quant format, RAM residency and KV/context
  size are non-levers here.'
tags:
- qwen
- flash-next
- moe
- rtx4090
- cpu-affinity
- ubatch-size
- n-cpu-moe
- mtp
- speculative-decoding
- kv-cache
- vram-paging
- benchmark-noise
updated: '2026-10-27'
author:
  model: qwen/qwen3.8-omni-flash
  conversation: 3680f0f3-decb-4f24-86d0-5200c0ba2a0e
researched: '2026-10-27'
sources:
- title: 'My 13 benchmark runs on profile qwen3.8-flash-next-24gb and clone flash-next-q4kxl-test, benchmark
    ''Tetris no details'' (reasoning medium), runtime b11815 cuda, 2026-10-27. Key runs: 9b1e7adc, e9dcfb4a,
    23c86122, 9fe63d17, ca358d21, 6cfb6778, 71840da0/004afab4, d14a7b9f, 323fccdd, 0eb66dbd/137bc58f'
- title: 'Unsloth, ''Qwen3.8-Flash-Next: How to Run Locally'' (architecture, quant KLD table, MTP 1.3-1.7x
    claim, shared-vs-general MTP modules, stock-vs-custom fork)'
  url: https://unsloth.ai/docs/models/qwen3.8-next
  accessed: '2026-10-27'
- title: 'unsloth/Qwen3.8-Flash-Next-GGUF model card rev 766911a: 125B total / 6B active / 51B n-gram
    / 4B MTP; 48 layers = 12 x (3 x (Gated DeltaNet -> MoE) + QSA -> MoE); 512 experts, 10 routed + 1
    shared, expert intermediate 640; hidden 2560; 262K native ctx'
  url: https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF
  accessed: '2026-10-27'
applies_to:
  models:
  - Qwen3.8-Flash-Next-UD-IQ4_XS
  - Qwen3.8-Flash-Next-UD-Q4_K_XL
  hardware:
  - NVIDIA GeForce RTX 4090 24 GB
  - Intel Core i7-14700F
  - 127.8 GiB system RAM
  runtime: llama.cpp b11815 cuda
confidence: medium
---

## Architecture, from the model card

Qwen3.8-Flash-Next is **125B total, ~6B activated**, plus **51B of n-gram embedding** parameters and a 4B MTP head. 48 layers laid out as `12 x (3 x (Gated DeltaNet -> MoE) + 1 x (QSA -> MoE))`, so **36 Gated-DeltaNet + 12 sparse-attention layers**, each followed by an MoE block of **512 experts, 10 routed + 1 shared, expert intermediate width 640**, hidden dim 2560. Native context 262,144.

The n-gram embedding is 20M bigram/trigram entries at layer 2, ~25.5 GiB at 4-bit. Unsloth states these are deliberately not quantised below 4-bit because access is random.

**File-size decomposition that reconciles everything** (UD-IQ4_XS, 87.25 GiB, 3 shards): 125B transformer params at 4-bit ≈ 62.5 GiB + 51B n-gram ≈ 25.5 GiB. UD-Q4_K_XL is 103.69 GiB / 4 shards (1.188x).

## The central finding: this model is CPU-placement-bound, not bandwidth-bound

With 38 of 48 expert layers on CPU (`--n-cpu-moe 38`), decode runs ~17-20 tok/s and the wall is **where the CPU threads run and how wide the batches are** — not memory traffic, not dequant cost.

Derived traffic, which is why the usual intuition fails:

- **Decode**: 38 layers x 11 experts x 3 matrices ≈ **1.03 GiB of expert weights per token** → ~18 GB/s at 18 tok/s, against ~90 GB/s available on DDR5. **Not bandwidth-bound.**
- **Prefill**: llama.cpp batches tokens per expert, so each of the 512 experts is read once per ubatch → ~0.6 GB/s. Nowhere near any limit. At ~6 GFLOP/token, 250 tok/s ≈ **1.5 TFLOPS — the i7-14700F's AVX2 roofline** (this chip has no AVX-512).

So prefill was saturating CPU *compute* while sitting on a trivial memory footprint, and decode was latency/dispatch-bound on ~1,250 tiny GEMVs per token. Both are fixed by CPU placement and batching, which is what worked.

## What worked (measured, cumulative)

Baseline 254.55 prefill / 16.78 decode → **535-573 prefill / 19.71 decode**.

| change | effect | why |
|---|---|---|
| `--cpu-range 0-15 --threads 16` (P-cores only) | decode +8.9%, prefill +3% | 14700F enumerates 8 P-cores as logical 0-15 (HT siblings), 12 E-cores as 16-27. E-cores have ~half the AVX2 throughput per thread |
| `--cpu-range-batch 0-27 --threads-batch 28` | prefill +2% | prefill is FLOP-bound, so more cores help even if they are E-cores |
| `--ubatch-size 512 -> 2048` | **prefill +125%** (268 -> 573) | more tokens per expert converts tiny GEMVs into wide GEMMs. Biggest single lever |
| `--cpu-strict 1` | decode +5.6% | without it the Windows scheduler migrates the pinned gen threads back onto E-cores |
| `--threads 16 -> 20` (before pinning) | decode +5.9% | see caveat below |

**Order matters.** `--threads 20` helped before pinning (16.78 → 17.77) but once generation is pinned to the 16 P-core logical CPUs, 20 threads is wrong. The final config is threads 16 + pin + strict.

`--threads 8` on the pinned P-cores **collapsed decode** (18.11 → 15.00) with prefill untouched: both HT siblings per P-core earn their keep. Do not reduce threads to cut dispatch overhead.

## What did not work

- **MTP speculative decoding: no gain.** With the draft confirmed loaded and running, `--spec-type draft-mtp --spec-draft-n-max 5 --spec-draft-ngl 999` gave decode 16.68 vs 16.78 baseline. Speculation amortises weight reads when the model is GPU-bandwidth-saturated; with experts in system RAM there is nothing to amortise, and the verification pass costs the same CPU expert traffic. Unsloth's 1.3-1.7x is measured with the model GPU-resident (RTX 6000 PRO).
- **Quant format is not a lever.** UD-Q4_K_XL at equal VRAM (`--n-cpu-moe 41`, needed to avoid overrun) added **+39% CPU-resident bytes and moved decode not at all** (18.03 vs 17.77), while prefill fell 20% (3 more layers off the GPU). This kills both the "fewer bytes" and the "block quant dequantises cheaper" theories at once. Note Q4_K_XL is the *higher-quality* quant (Unsloth KLD: top-1 92.255 vs IQ4_XS 89.554), so it remains a legitimate quality-driven swap — just not a speed one.
- **RAM residency / `--load-mode`.** `mmap+mlock` cost prefill −39% (157.53) for +3.4% decode and a 275 s load. Reject.
- **`--poll 100`**: decode +3.1%, prefill −9.6%. Busy-polling steals cycles from a compute-saturated prefill. Reject.
- **`--prio 2`**: two samples 20.50 and 17.24 — mean 18.87, *below* the 19.71 measured without it. Reject, and it spends other processes' latency.

## Memory facts: context is nearly free, VRAM is the hard wall

- **KV cache ≈ 2.1 GiB at 196K with q8_0 (~11 KB/token)** — hybrid linear + sparse attention pays off enormously. Bracketed by two runs: 196K/q8_0 = 21.54 GiB VRAM, 65K/q4_0 = 19.79 GiB with a 2.59 GiB draft removed. Consequence: **do not trim context to buy VRAM headroom**; it returns almost nothing and costs a capability. 196K (or the native 262K) is cheap here.
- **VRAM is the binding constraint on speed**, because it caps how many expert layers can be GPU-resident, and GPU-resident expert layers are what prefill and the ubatch lever act on. Baseline sits at 21.54 of ~23.0 GiB usable, so only ~1.3 GiB of headroom exists — roughly **one** expert layer (`--n-cpu-moe` 38 → 37) at IQ4_XS sizes.
- **`--ubatch-size` costs VRAM**: 512 → 1024 was +0.10 GiB, 1024 → 2048 was +0.87 GiB (non-linear). 2048 landed at 22.6-22.7 GiB, which is the practical ceiling; 4096 would overrun.
- **RSS is not comparable across `--load-mode` values.** `none` reads the file into private anonymous memory (52.85 GiB RSS for an 87 GiB file); `mmap+mlock` leaves it file-backed (6.45 GiB RSS). RSS there measures accounting, not residency. This is a trap for anyone inferring what is resident from RAM figures.

## VRAM overrun presents as a prefill collapse, with no OOM in the log

Loading a 2.59 GiB draft onto a profile already at 21.54/23.0 GiB produced peak **23.30 GiB, prefill −42%, decode −17%**, and `decode_low_tokens_per_second` 0.01 (vs 15.36 healthy). On Windows/WDDM the driver pages rather than failing. A second symptom: **first-token time 81.5 s for a 2.2K-token prompt**, vs 9.05 s for a *larger* 14.9K context in a healthy run.

Diagnostic habit: check peak VRAM against device capacity **first**; a prefill regression with unchanged attention settings is paging until proven otherwise. Also note a user-set `--n-gpu-layers` disables auto-fitting entirely (`common_fit_params: n_gpu_layers already set by user to 999, abort`), so llama.cpp will not rescue an over-subscribed profile.

## Shared MTP modules will not load on stock llama.cpp

`mtp-*-shared-*` omit `token_embd.weight` by design (they share it with the main model) and fail with `check_tensor_dims: tensor 'token_embd.weight' not found`. This kills both shared variants (1.78 / 2.6 GiB) — the only ones small enough for a 24 GB card. Only the general module (2.59 GiB) loads, and it buys nothing here anyway. Unsloth's own llama-server MTP example uses a shared module and requires their custom fork.

## Noise calibration for this benchmark

The Tetris benchmark is agentic; each run chooses its own workload. **Do not chase token counts** — judge by prefill/decode rate. But decode variance across near-identical configs is large: 17.24-20.50 (±8%) on the final config family, and `--prio 2` alone returned 20.50 then 17.24. **Any single-sample decode claim under ~8% is unreliable; take two samples before keeping or rejecting.** Prefill is tighter within a config (535-573 across three samples at ubatch 2048) but showed one 368 outlier.

Load time is dominated by page-cache state: 182 s cold → ~20 s warm for the same 87 GiB file. Never read a load-time change as a settings effect.

## Contrast with Qwen3.8-27B on the same machine

The 27B is a GPU-resident dense model, bandwidth-saturated at ~1.2 TB/s effective. Nearly every conclusion in its research doc (`qwen3-27b-mtp-spec-decode-rtx4090`) **inverts** here:

| | 27B (GPU-resident dense) | Flash-Next (experts in RAM) |
|---|---|---|
| binding wall | GPU memory bandwidth | CPU placement + AVX2 compute |
| `--threads` | no-op | **+6-9%** |
| CPU affinity | n/a | **+9-17%** |
| `--ubatch-size` | −2% decode, reject | **+125% prefill** |
| MTP spec-decode | large gain | **nothing** |
| KV cache type | q4_0 essential | irrelevant (~2 GiB at 196K q8_0) |
| smaller quant | only lever, but dequant cancels it | not a lever at all |

**Do not transfer dense-GPU-resident tuning recipes to a large MoE with CPU-offloaded experts.** Ask first which resource the model actually saturates.

## If continuing

1. **`--n-cpu-moe 37`** is the one untested move with a real mechanism (one more expert layer GPU-resident, ~1.25 GiB) but it needs ~0.1 GiB more than the ~1.3 GiB headroom at 22.7 GiB. Only viable by dropping 196K → 131K (returns ~0.7 GiB of KV) and accepting the capability loss. Expect low single-digit decode.
2. **A newer llama.cpp** may change the picture: `--repack` and CUDA MoE batching are both active areas, and a build with better CPU MoE kernels would raise the whole ceiling. Current latest release is b11669; this machine runs dev b11815.
3. **Unsloth's custom fork** (`qwen4exp/mtp` branch) is the only route to the shared MTP modules, but see above for why MTP is expected to remain worthless with experts off-GPU.
