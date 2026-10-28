---
title: Qwen3.6-35B-A3B performance reports on RTX 4090
slug: qwen36-35b-a3b-4090-community-benchmarks
summary: Public results provide useful ceilings and leads for this profile, though neither directly matches
  LlamaWebUI's 11K-token agentic Tetris workload. An RTX 4090 llama.cpp run reported 160.6 tok/s for Q4_K_M,
  and a separate llama-bench page reported 188±23 tok/s Q4_K_XL generation at 32 generated tokens, with
  GPU/model details not independently verified as a 4090.
tags:
- qwen3.6
- qwen
- rtx4090
- llama.cpp
- benchmark
- q4_k_xl
updated: '2026-10-28'
author:
  model: openai/gpt-6-luna
  conversation: 0481d2a9-1688-4233-90fd-bb04cfedc25a
researched: '2026-10-28'
sources:
- title: 'Can an RTX 4090 run Qwen3.6 35B-A3B? llm-speed.com: signed llama.cpp Q4_K_M run'
  url: https://llm-speed.com/can-run/qwen3-6-35b-a3b/rtx-4090
  accessed: '2026-10-28'
- title: 'knoopx/Qwen3.6-35B-A3B-NVFP4-GGUF model card: llama-bench NVFP4 vs Q4_K_XL results'
  url: https://huggingface.co/knoopx/Qwen3.6-35B-A3B-NVFP4-GGUF
  accessed: '2026-10-28'
applies_to:
  models:
  - Qwen3.6-35B-A3B-UD-Q4_K_XL
  hardware:
  - NVIDIA GeForce RTX 4090 24 GB
  runtime: llama.cpp
confidence: low
---

## Public measurements

- llm-speed's RTX 4090 page reports one signed Q4_K_M llama.cpp run on build 8931: 160.6 tok/s decode, 464.3 tok/s prefill for a 112-token chat prompt; its 3,182-token chat-long run reports 160.4 decode and 4,588.8 prefill. These are not Q4_K_XL nor comparable to the local Tetris benchmark. The page has no VRAM measurement.
- The Hugging Face card for `knoopx/Qwen3.6-35B-A3B-NVFP4-GGUF` reports llama-bench results on a single GPU, build `fc2b0053f / 8967`, context sizes 2K–8K, 32 generation tokens, and model size 20.81 GB for Q4_K_XL. Q4_K_XL: 9,121±33 / 8,889±47 / 8,464±48 prompt tok/s and 188±23 generation tok/s. NVFP4: 9,767±67 / 9,572±16 / 9,107±14 prompt tok/s and 136±19 generation tok/s. The card does not identify that test GPU as an RTX 4090, so those figures should not be treated as confirmed 4090 results.

## Tuning implications

Treat ~160 tok/s public RTX 4090 Q4_K_M and 188±23 tok/s single-GPU Q4_K_XL as directional comparison points only. Their short prompts, very short generation, older build, and distinct harness differ from the local benchmark. The local measured Q4_K_XL baseline remains authoritative for changes to this profile. The most plausible tuning effort is ensuring full GPU residency and exploring batch/microbatch and attention memory settings while monitoring VRAM headroom.
