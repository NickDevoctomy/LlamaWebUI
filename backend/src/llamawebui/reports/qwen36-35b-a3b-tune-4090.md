---
title: 'Qwen3.6-35B-A3B on RTX 4090: prefill tuning, MTP, and Q8 trial'
slug: qwen36-35b-a3b-tune-4090
summary: On Q4_K_XL, larger microbatches with Flash Attention substantially improved observed prefill,
  while standard decode remained near the 114 tok/s baseline. Two-token MTP on Q4 observed 119–121 tok/s
  decode but slower prefill and higher RAM, with one completed and one tool-limit run; four tokens hit
  context capacity. Q8_0 completed at about 50 tok/s decode and 692 tok/s prefill; Q8 MTP repeated at
  53.72 and 54.62 tok/s, with slower prefill and more RAM.
tags:
- qwen3.6
- rtx4090
- llama.cpp
- ubatch-size
- flash-attention
- mtp
- q8_0
- benchmark
updated: '2026-10-28'
author:
  model: openai/gpt-6-luna
  conversation: 0481d2a9-1688-4233-90fd-bb04cfedc25a
created: '2026-10-28'
subject:
  profile: qwen3.6-35b-a3b
  model: Qwen3.6-35B-A3B-UD-Q4_K_XL
hardware:
- Intel Core i7-14700F
- NVIDIA GeForce RTX 4090 24 GB
- 127.8 GiB system RAM
runtime: b11815 cuda (0.7.0-dev)
benchmark: Tetris no details (reasoning medium)
outcome: improved
results:
- metric: prefill_tokens_per_second
  before: 2198.14
  after: 3370.94
  unit: tok/s
- metric: decode_tokens_per_second
  before: 114.06
  after: 112.6
  unit: tok/s
- metric: load_seconds
  before: 10.55
  after: 10.36
  unit: s
- metric: peak_vram
  before: 21.67
  after: 21.6
  unit: GiB
- metric: peak_ram
  before: 2.79
  after: 3.66
  unit: GiB
alternatives:
- name: MTP Q4_K_XL two-token candidate (unconfirmed)
  profile: qwen36-35b-mtp-test
  summary: 'Two tokens: run 13a3ed1f completed at 120.62 tok/s decode / 1,727.97 tok/s prefill; run a494c907
    observed 119.13 decode / 1,986.12 prefill but ended at the tool-round limit. Four-token run hit context
    capacity and failed. Keep separate and at two tokens.'
  results:
  - metric: decode_tokens_per_second
    before: 114.06
    after: 119.88
    unit: tok/s
  - metric: prefill_tokens_per_second
    before: 3370.94
    after: 1857.05
    unit: tok/s
  - metric: peak_vram
    before: 21.6
    after: 21.19
    unit: GiB
  - metric: peak_ram
    before: 3.66
    after: 5.83
    unit: GiB
- name: Q8_0 standard, fit on, ubatch 4096
  profile: qwen36-35b-q8-test
  summary: Q8_0 fits, but decodes around 50 tok/s. At ubatch 4096, prefill reached 692 tok/s; 1024-microbatch
    run had 458 tok/s prefill.
  results:
  - metric: decode_tokens_per_second
    before: 114.06
    after: 49.82
    unit: tok/s
  - metric: prefill_tokens_per_second
    before: 3370.94
    after: 692.28
    unit: tok/s
  - metric: load_seconds
    after: 10.12
    unit: s
  - metric: peak_vram
    after: 21.58
    unit: GiB
  - metric: peak_ram
    after: 5.15
    unit: GiB
- name: Q8_0 MTP, two tokens, fit on, ubatch 4096
  profile: qwen36-35b-q8-mtp-test
  summary: Two completed samples show 53.72 and 54.62 tok/s decode, versus standard Q8's 49.82 in one
    sample. MTP prefill was 516.88 and 559.59 tok/s, below standard Q8's 692.28; RAM 6.91 and 6.56 GiB,
    VRAM 21.18 GiB. Likely a small decode improvement but still far below Q4 speed.
  results:
  - metric: decode_tokens_per_second
    before: 49.82
    after: 54.17
    unit: tok/s
  - metric: prefill_tokens_per_second
    before: 692.28
    after: 538.24
    unit: tok/s
  - metric: load_seconds
    after: 11.87
    unit: s
  - metric: peak_vram
    after: 21.18
    unit: GiB
  - metric: peak_ram
    after: 6.74
    unit: GiB
left_behind:
- Subject Q4 profile qwen3.6-35b-a3b remains ctx 131072, ubatch 4096, flash-attn on, 16 threads, cpu-range
  0-15, cpu-strict 1, op-offload enabled.
- Downloaded MTP Q4_K_XL (21.28 GiB), Q8_0 base (34.37 GiB), and Q8_0 MTP (35.21 GiB).
- 'Separate enabled test profiles retained: qwen36-35b-mtp-test (Q4 MTP, two tokens), qwen36-35b-q8-test
  (Q8_0, ubatch 4096), qwen36-35b-q8-mtp-test (Q8_0 MTP, two tokens, ubatch 4096). Router left stopped
  as at start.'
---

## Starting point and external context

Original Q4_K_XL profile used `--ctx-size 131072` only. Fresh medium Tetris baseline `aa3f65e1-b8d3-4d40-b8dc-eeadb1af4b5d`: 10.55 s load, 2,198.14 tok/s prefill, 114.06 tok/s decode, 2.79 GiB peak RAM and 21.67 GiB peak VRAM. Router was stopped at start and remains stopped.

Public results saved in research `qwen36-35b-a3b-4090-community-benchmarks`: RTX 4090 Q4_K_M was reported at 160.6 tok/s on a short prompt, while a separate single-GPU Q4_K_XL llama-bench report showed 188±23 tok/s on a GPU not identified. Neither directly matches this long agentic test.

## Main-profile Q4 tuning

| Configuration | Run | Load s | Prefill tok/s | Decode tok/s | RAM GiB | VRAM GiB |
|---|---|---:|---:|---:|---:|---:|
| Fresh baseline defaults | `aa3f65e1` | 10.55 | 2,198.14 | 114.06 | 2.79 | 21.67 |
| ubatch 1024 | `b32afd2c` | 10.39 | 2,681.18 | 109.04 | 3.91 | 21.54 |
| ubatch 2048 | `192719a4` | 9.47 | 2,790.68 | 105.79 | 3.99 | 21.60 |
| 2048 + Flash Attention | `988adaed` | 10.23 | 2,959.82 | 106.35 | 3.98 | 21.60 |
| + P-core pin, strict, 16 threads | `19fdd32d` | 9.98 | 3,454.27 | 107.48 | 3.75 | 21.60 |
| + 8 threads | `6976ec2a` | 10.31 | 2,718.41 | 109.08 | 4.46 | 21.60 |
| + op-offload, 16 threads | `de2826c7` | 10.38 | 3,118.29 | 113.62 | 4.19 | 21.60 |
| ubatch 4096 + FA + affinity + op-offload (final subject) | `45db131b` | 10.36 | **3,370.94** | 112.60 | **3.66** | 21.60 |

The final Q4 profile settings are `ctx-size 131072`, `ubatch-size 4096`, `flash-attn on`, `threads 16`, `cpu-range 0-15`, `cpu-strict 1`, `op-offload`. Against fresh baseline this measured +53.4% prefill, decode -1.3%, approximately unchanged VRAM, and +0.87 GiB RAM. No reproducible standard-profile decode speedup was established.

```chart
{"type":"bar","title":"Q4 tuning: prefill by configuration","categories":["baseline","ubatch 1024","ubatch 2048","2048 + FA","P-core pinned","8 threads","op-offload","ubatch 4096 final"],"series":[{"name":"prefill tok/s","values":[2198.14,2681.18,2790.68,2959.82,3454.27,2718.41,3118.29,3370.94]}],"y_label":"tok/s"}
```

```chart
{"type":"bar","title":"Q4 tuning: decode by configuration","categories":["baseline","ubatch 1024","ubatch 2048","2048 + FA","P-core pinned","8 threads","op-offload","ubatch 4096 final"],"series":[{"name":"decode tok/s","values":[114.06,109.04,105.79,106.35,107.48,109.08,113.62,112.6]}],"y_label":"tok/s","targets":[{"label":"fresh baseline","value":114.06}]}
```

## MTP Q4 alternative and rejected four-token setting

Downloaded same-quant Unsloth MTP Q4_K_XL and tested a separate clone. Two-token run `13a3ed1f` completed at 120.62 tok/s decode, 1,727.97 tok/s prefill, 10.95 s load, 5.72 GiB RAM / 21.18 GiB VRAM, elapsed 201.95 s and context peak 47,413. Second run `a494c907` observed 119.13 decode and 1,986.12 prefill but ended at the benchmark tool-round limit after 280.23 s. Four-token run `c486f2ab` reached context 131,071 / 131,072 and failed; it is invalid and was cancelled. The MTP Q4 clone is reset to two tokens, and remains a separate candidate, not the chosen original.

## Q8_0 trials

Downloaded standard Q8_0 (34.37 GiB) and MTP Q8_0 (35.21 GiB). Tested clones with `--fit on`, Flash Attention, P-core affinity, strict placement and context 131,072; microbatch initially 1024, then 4096.

| Q8 setup | Run | Status | Load s | Prefill tok/s | Decode tok/s | RAM GiB | VRAM GiB |
|---|---|---|---:|---:|---:|---:|---:|
| Standard Q8, ubatch 1024 | `fdeefb03` | completed | 11.00 | 457.93 | 51.58 | 5.11 | 21.47 |
| Standard Q8, ubatch 4096 | `b7ecc58b` | completed | 10.12 | 692.28 | 49.82 | 5.15 | 21.58 |
| Q8 MTP, two tokens, ubatch 4096 | `711744c1` | completed | 12.03 | 516.88 | 53.72 | 6.91 | 21.18 |
| Q8 MTP repeat | `04221935` | completed | 11.70 | 559.59 | 54.62 | 6.56 | 21.18 |

Q8 MTP has two consistent decode observations averaging **54.17 tok/s** (vs a single standard Q8 4096 sample at 49.82), but its prefill average is 538.24 tok/s (below standard Q8's 692.28), RAM average 6.74 GiB and VRAM 21.18 GiB. This is modest Q8 decode improvement, not near Q4's 112.60 tok/s. The decode difference should be treated as directional because only one standard Q8 sample was taken. Q8 MTP completed both runs without compaction.

```chart
{"type":"bar","title":"Quant and MTP alternatives: decode","categories":["Q4 baseline","Q4 tuned","Q4 MTP 2 #1","Q4 MTP 2 #2*","Q8 ubatch 1024","Q8 ubatch 4096","Q8 MTP #1","Q8 MTP #2"],"series":[{"name":"decode tok/s","values":[114.06,112.6,120.62,119.13,51.58,49.82,53.72,54.62]}],"y_label":"tok/s","targets":[{"label":"Q4 tuned","value":112.6}]}
```

*Second Q4 MTP result stopped at the tool-round limit, so not a normally completed task.

## Final state and recommended choices

- Requested profile `qwen3.6-35b-a3b` stays on tuned Q4_K_XL: ubatch 4096, FA on, 16 threads, CPU range 0-15, strict, op-offload.
- Optional Q4 MTP profile `qwen36-35b-mtp-test` is set to two tokens; do not use four tokens for this benchmark.
- Optional standard Q8 profile `qwen36-35b-q8-test` at 4096 ubatch.
- Optional Q8 MTP profile `qwen36-35b-q8-mtp-test` at two tokens and 4096 ubatch. It appears to modestly improve Q8 decode at the cost of prefill and RAM.
- Profiles are enabled but use no memory until loaded. Router remains stopped as found. Downloads retained for all candidates.
