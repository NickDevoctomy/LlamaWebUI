---
title: Community Qwen3.8-27B MTP and plain decode results on RTX 4090
slug: qwen38-27b-4090-community-mtp-speed
summary: Public RTX 4090 reports show Qwen3.8-27B Q4_K_M around 40–43 tok/s plain and about 73–85 tok/s
  with MTP on shorter suite workloads; test conditions differ from local Tetris benchmarking, and draft-depth
  settings are mostly undisclosed. The local profile's existing MTP measurements are substantially higher,
  so public figures are context rather than targets.
tags:
- qwen3.8
- mtp
- speculative-decoding
- rtx4090
- benchmark
updated: '2026-10-29'
author:
  model: openai/gpt-6-luna
  conversation: 82e4a98f-b544-485e-aa74-21643ba1b50a
researched: '2026-10-29'
sources:
- title: MTP speculative decoding on an RTX 4090 · llm-speed
  url: https://llm-speed.com/blog/mtp-speculative-decoding-rtx-4090
  accessed: '2026-10-29'
- title: 'llama.cpp on NVIDIA RTX 4090 (24GB): tokens per second | llamaperf'
  url: https://llamaperf.com/engine/llama-cpp/rtx-4090
  accessed: '2026-10-29'
applies_to:
  models:
  - Qwen3.8-27B Q4_K_M
  hardware:
  - NVIDIA GeForce RTX 4090 24 GB
  runtime: llama.cpp
confidence: low
---

## Public reference results

- llm-speed's October 14, 2026 comparison on an RTX 4090 24 GB with Qwen3.8-27B Q4_K_M and llama.cpp 0.6.0-dev reported MTP off/on decode of 43.06/84.51 tok/s (chat-short), 42.63/73.10 (chat-long), and 42.07/77.60 (agent-trace). Prompt processing also fell with MTP enabled: e.g. chat-short 428.6→299.3 tok/s. The page says runs were community-submitted, and MTP draft depth/settings were not recorded. These are not equivalent to LlamaWebUI's Tetris benchmark.
- llamaperf reports 40.0 tok/s median for three plain Qwen3.8 27B 4-bit llama.cpp runs on RTX 4090 across contexts 8K–194K, and lists a Q4_K_M single result at 40 tok/s. The page's reports span different builds and contexts, so treat as rough reference only.
- llamaperf's page also lists heterogeneous Qwen3.8 27B MTP results, including 80 tok/s Q4_K_M and 90–115 tok/s IQ4_XS, but does not give enough settings/context to compare closely.

## Implication

MTP can materially improve decode at the cost of slower prefill and later first token, but published numbers vary by workload/context and do not specify depth. A locally controlled comparison across draft maximums is needed; use repeated samples and report the arithmetic mean, while noting the agentic workload's run-to-run variability.
