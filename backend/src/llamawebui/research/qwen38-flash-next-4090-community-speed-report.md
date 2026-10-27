---
title: Community RTX 4090 results for Qwen3.8-Flash-Next with llama.cpp
slug: qwen38-flash-next-4090-community-speed-report
summary: A community benchmark repo reports about 34 tok/s decode on an RTX 4090 at the 24-GB tier using
  Qwen3.8-Flash-Next, CPU-offloaded MoE experts, CPU token-embedding placement, and different loading/microbatch
  settings; this is a useful lead but not directly comparable to LlamaWebUI's Tetris run or its profile.
tags:
- qwen
- flash-next
- rtx4090
- llama.cpp
- benchmark
- cpu-offload
updated: '2026-10-27'
author:
  model: openai/gpt-6-luna
  conversation: ab703ced-ab4c-49f2-a89a-01d8217550f4
researched: '2026-10-27'
sources:
- title: lukaLLM/Qwen3.8-Flash-Next-VRAM-Benchmark README
  url: https://raw.githubusercontent.com/lukaLLM/Qwen3.8-Flash-Next-VRAM-Benchmark/master/README.md
  accessed: '2026-10-27'
applies_to:
  models:
  - Qwen3.8-Flash-Next
  hardware:
  - NVIDIA GeForce RTX 4090
  runtime: llama.cpp (community build/version not stated in README excerpt)
confidence: low
---

## Reported speed and configuration

The README reports roughly 34 tok/s decode for an RTX 4090 / 24-GB-VRAM setup, and says a 64-GB system-RAM cap and a 56-GB cap gave 34.4 and 34.7 tok/s. Its recommended 24-GB setup uses `--n-cpu-moe 42`, `-ot per_layer_token_embd=CPU`, `--load-mode mmap`, `--lazy-mode on`, `-ub 512`, and no MTP. In the same repository's wider tier ladder, decode rises from 39.0 tok/s at 24 GB to 109.1 tok/s at 96 GB; a separate 24-GB note says MTP slows decode from 34.7 to 10.2 tok/s. The repository also describes `--load-mode none` as a major prefill lever in its separate experiment.

These numbers are from a different test harness and setup; they are not comparable to the local 'Tetris no details' benchmark. The README excerpt does not identify the exact quantization, llama.cpp commit, prompt context, or measurement methodology for the 34 tok/s figure. Treat its CPU token-embedding placement / lower VRAM pressure / expert split as hypotheses to test locally, not as evidence of an expected Tetris speed.
