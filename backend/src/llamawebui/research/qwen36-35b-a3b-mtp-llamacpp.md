---
title: Qwen3.6-35B-A3B MTP setup and RTX 4090 test
slug: qwen36-35b-a3b-mtp-llamacpp
summary: Qwen3.6-35B-A3B MTP works in llama.cpp with an MTP GGUF and `draft-mtp`. On this RTX 4090, two
  draft tokens produced 120.62 tok/s in one completed Tetris run; a second two-token run observed 119.13
  tok/s but ended at the benchmark's tool-round limit. This is a promising decode option but has lower
  prefill and higher RAM than the base model; four tokens ran to the context cap and failed, so avoid
  four tokens for this workload.
tags:
- qwen3.6
- mtp
- speculative-decoding
- llama.cpp
- rtx4090
- benchmark
updated: '2026-10-28'
author:
  model: openai/gpt-6-luna
  conversation: 0481d2a9-1688-4233-90fd-bb04cfedc25a
researched: '2026-10-28'
sources:
- title: Unsloth Qwen3.6 MTP GGUF repository card
  url: https://huggingface.co/unsloth/Qwen3.6-35B-A3B-MTP-GGUF
  accessed: '2026-10-28'
- title: 'Unsloth Qwen3.6 local inference guide: MTP configuration and draft-length guidance'
  url: https://unsloth.ai/docs/models/qwen3.6
  accessed: '2026-10-28'
- title: llama.cpp speculative decoding documentation
  url: https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md
  accessed: '2026-10-28'
- title: 'LlamaWebUI measurements on qwen36-35b-mtp-test: runs 13a3ed1f (2-token completed), a494c907
    (2-token tool-round limit), c486f2ab (4-token context cap); Tetris no details, medium reasoning, RTX
    4090, b11815 CUDA'
applies_to:
  models:
  - Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL
  hardware:
  - NVIDIA GeForce RTX 4090 24 GB
  - Intel Core i7-14700F
  - 127.8 GiB system RAM
  runtime: llama.cpp b11815 cuda
confidence: medium
---

## Use of MTP

Use an MTP-enabled Qwen3.6 GGUF and `--spec-type draft-mtp`. The Unsloth card's example uses `--spec-draft-n-max 2`; its guide says to test 1–6, with 2 often effective but hardware-dependent. MTP requires more headroom. The cited card warns that `-np > 1` and `--mmproj` are unsupported with MTP.

## RTX 4090 local evidence

MTP Q4_K_XL with two draft tokens:

- Completed Tetris medium run `13a3ed1f-2e3d-4ed7-aa91-9003b30a0824`: 120.62 tok/s decode, 1,727.97 tok/s prefill, 10.95 s load, 5.72 GiB RAM, 21.18 GiB VRAM; task completed in 201.95 s with peak context 47,413 and no compaction.
- Follow-up `a494c907-0778-4548-a3c2-156d5328f922`: 119.13 tok/s decode, 1,986.12 tok/s prefill, 9.44 s load, 5.93 GiB RAM, 21.20 GiB VRAM; task stopped at the 24-tool-call limit after 280.23 s, at context peak 81,024, no compaction. Treat this as unfinished, not a clean full-task comparison.

The two samples' observed decode rates are close (~120 tok/s) and above the base-model 114.06 tok/s fresh baseline, but variable agentic workload and the unfinished second task mean the gain remains suggestive rather than conclusive. MTP prefill is slower and RAM usage higher; keep it as a separate option rather than silently replacing the base profile.

Four-token follow-up `c486f2ab-aa91-4231-b35d-9da081983071` reached context peak 131,071/131,072, was truncated, then failed with read error after 1,140 s. Server log showed ~121.1 tok/s during its very long run and mean draft length 4.99, but that is not a valid completed benchmark. Avoid four-token MTP in this agentic benchmark.
