---
title: 'Qwen3.6-35B-A3B on RTX 4090: prefill tuning and MTP candidate'
slug: qwen36-35b-a3b-tune-4090
summary: On Q4_K_XL, larger microbatches with Flash Attention substantially improved observed prefill,
  while decode stayed near the 114 tok/s baseline; CPU affinity did not improve decode. A same-quant MTP
  clone with two draft tokens observed 119–121 tok/s decode across two runs, but the second ended at the
  benchmark tool-round limit; keep MTP as an unconfirmed alternative. Four tokens drove the agentic workload
  to the context limit and failed, so the clone is set back to two tokens.
tags:
- qwen3.6
- rtx4090
- llama.cpp
- ubatch-size
- flash-attention
- mtp
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
- name: MTP Q4_K_XL two-token candidate (unconfirmed, not replacement)
  profile: qwen36-35b-mtp-test
  summary: Two-token MTP, same tuned settings. First run completed at 120.62 tok/s decode / 1,727.97 tok/s
    prefill in 201.95 s. Second run observed 119.13 decode / 1,986.12 prefill but terminated at benchmark
    tool-round limit after 280.23 s; not a clean completed task. Higher RAM usage and slower prefill than
    base. Clone remains enabled and set to two tokens.
  results:
  - metric: decode_tokens_per_second
    before: 114.06
    after: 119.88
    unit: tok/s
  - metric: prefill_tokens_per_second
    before: 3370.94
    after: 1857.05
    unit: tok/s
  - metric: load_seconds
    before: 10.36
    after: 10.2
    unit: s
  - metric: peak_vram
    before: 21.6
    after: 21.19
    unit: GiB
  - metric: peak_ram
    before: 3.66
    after: 5.83
    unit: GiB
left_behind:
- Subject profile qwen3.6-35b-a3b remains at ubatch-size 4096, flash-attn on, threads 16, cpu-range 0-15,
  cpu-strict 1, op-offload enabled; original model file retained.
- Downloaded unsloth/Qwen3.6-35B-A3B-MTP-GGUF UD-Q4_K_XL (21.28 GiB) and enabled test profile qwen36-35b-mtp-test
  retained at two draft tokens for further optional use.
- Router left stopped, as at start.
---

## Starting point

Original Q4_K_XL profile used `--ctx-size 131072` only. Fresh matched Tetris no-details / medium baseline `aa3f65e1-b8d3-4d40-b8dc-eeadb1af4b5d`: 10.55 s load, 2,198.14 tok/s prefill, 114.06 tok/s decode, 2.79 GiB peak RAM, 21.67 GiB peak VRAM. Router was stopped at start and remains stopped.

Public context is in `qwen36-35b-a3b-4090-community-benchmarks`: public RTX 4090 Q4_K_M reported 160.6 tok/s at short prompts; a separate GPU-unspecified Q4_K_XL test reported 188±23 tok/s at 32 generated tokens. Neither is directly comparable to this long agentic benchmark.

## Main-profile tuning

| Configuration | Run | Load s | Prefill tok/s | Decode tok/s | RAM GiB | VRAM GiB | Decision |
|---|---|---:|---:|---:|---:|---:|---|
| Baseline defaults | `aa3f65e1` | 10.55 | 2,198.14 | 114.06 | 2.79 | 21.67 | reference |
| ubatch 1024 | `b32afd2c` | 10.39 | 2,681.18 | 109.04 | 3.91 | 21.54 | prefill +22% |
| ubatch 2048 | `192719a4` | 9.47 | 2,790.68 | 105.79 | 3.99 | 21.60 | prefill +27% |
| 2048 + Flash Attention | `988adaed` | 10.23 | 2,959.82 | 106.35 | 3.98 | 21.60 | prefill +35% |
| + P-core affinity, strict, 16 threads | `19fdd32d` | 9.98 | 3,454.27 | 107.48 | 3.75 | 21.60 | prefill +57%, no decode gain |
| + 8 threads | `6976ec2a` | 10.31 | 2,718.41 | 109.08 | 4.46 | 21.60 | rejected |
| + op-offload, 16 threads | `de2826c7` | 10.38 | 3,118.29 | 113.62 | 4.19 | 21.60 | decode near baseline |
| ubatch 4096 + FA + affinity + op-offload | `45db131b` | 10.36 | **3,370.94** | 112.60 | **3.66** | 21.60 | final subject configuration |

Final subject settings: `ctx-size 131072`, `ubatch-size 4096`, `flash-attn on`, `threads 16`, `cpu-range 0-15`, `cpu-strict 1`, `op-offload`. Compared with fresh baseline this is +53.4% prefill, decode -1.3%, load -1.8%, peak VRAM essentially unchanged and RAM +0.87 GiB. Each change was measured on the subject; no reproducible standard-profile decode gain established.

```chart
{"type":"bar","title":"Measured prefill speed","categories":["baseline","ubatch 1024","ubatch 2048","2048 + FA","P-core pinned","8 threads","op-offload","ubatch 4096 final"],"series":[{"name":"prefill tok/s","values":[2198.14,2681.18,2790.68,2959.82,3454.27,2718.41,3118.29,3370.94]}],"y_label":"tok/s"}
```

```chart
{"type":"bar","title":"Measured decode speed","categories":["baseline","ubatch 1024","ubatch 2048","2048 + FA","P-core pinned","8 threads","op-offload","ubatch 4096 final"],"series":[{"name":"decode tok/s","values":[114.06,109.04,105.79,106.35,107.48,109.08,113.62,112.6]}],"y_label":"tok/s","targets":[{"label":"fresh baseline","value":114.06}]}
```

## MTP candidate, and the bad four-token choice

Downloaded the same-quant Unsloth Q4_K_XL MTP GGUF; made separate clone `qwen36-35b-mtp-test` and measured two draft tokens.

| Run | Status | Load s | Prefill tok/s | Decode tok/s | RAM GiB | VRAM GiB | Context peak / duration |
|---|---|---:|---:|---:|---:|---:|---|
| `13a3ed1f` | completed | 10.95 | 1,727.97 | 120.62 | 5.72 | 21.18 | 47,413; 201.95 s |
| `a494c907` | tool-round-limit (unfinished) | 9.44 | 1,986.12 | 119.13 | 5.93 | 21.20 | 81,024; 280.23 s |

The second two-token run terminated at 24 tool calls (`tool_round_limit`), so it is not a clean full-task completion. Both report observed decode around 119–121 tok/s, above the 114.06 base sample, but the benchmark is agentic and the evidence is suggestive, not conclusive. Mean of the two observed samples is 119.88 tok/s; mean prefill 1,857.05 tok/s, mean load 10.20 s, mean RAM 5.83 GiB and mean VRAM 21.19 GiB. The MTP option trades much slower prefill and about 2.2 GiB higher RAM for a possible decode gain.

The four-token run `c486f2ab` was a bad fit for this benchmark. It drove context to 131,071 / 131,072 and was truncated; the stream failed after 1,140 s. The server log showed ~121.1 tok/s during the very long generation and mean draft length 4.99, but it is not a valid completed benchmark. The operator cancelled it and explicitly identified it as the undesirable configuration. The clone is reset to **two** tokens, not four.

## Final state / next work

- Original profile remains enabled and unchanged from its tuned final settings listed above.
- MTP clone `qwen36-35b-mtp-test` remains enabled as an optional alternative, points at the downloaded MTP Q4_K_XL model and is configured with `draft-mtp`, `spec-draft-n-max 2`.
- MTP is not made the subject profile because only one of two runs completed the benchmark normally; retain it as an optional candidate for operator choice or further validation.
- Do not use four draft tokens for this agentic benchmark. Router remains stopped.
