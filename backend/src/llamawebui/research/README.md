# Research documents

These are what LlamaWebUI's assistant has learned about running models with llama.cpp: how arguments behave on particular hardware, what given models need, and llama.cpp changes worth knowing about. They ship with each release, so the assistant reads them before searching the web.

## How documents get here

1. The assistant writes documents to `data/research/` as it works (the `write_research` tool).
2. In the app, **Assistant > Research** lists them. From a source checkout, on the machine LlamaWebUI runs on, a user with `settings.write` can select local documents and publish them. Publishing moves them into this folder.
3. The maintainer reviews the files here and commits the ones worth shipping.

A document on a user's machine with the same slug as one here takes its place there, so a newer finding can replace a shipped one.

## Format

Markdown with YAML frontmatter. The app checks the frontmatter when it reads and writes a document, and skips files that don't pass (this README has no frontmatter, so it is skipped).

```yaml
---
title: Speculative decoding on a 24 GB NVIDIA GPU
slug: speculative-decoding-24gb-nvidia     # lowercase words and hyphens; the file is <slug>.md
summary: One or two sentences on what was found.
tags: [llama.cpp, speculative-decoding, nvidia]
researched: 2026-10-26                     # first written
updated: 2026-10-27                        # last rewritten
sources:                                   # at least one
  - title: llama.cpp server README
    url: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
    accessed: 2026-10-26
  - title: Benchmark run 2e15bfab (qwen3.8-27b, Tetris no details, b11815 cuda)
applies_to:
  models: [Qwen3.8-27B]
  hardware: [RTX 4090]
  runtime: b11815
confidence: medium                         # high, medium or low
author:
  model: anthropic/claude-sonnet-5.5
  conversation: 3ceecfcf-0a78-40a1-bb71-1253c0cc8cba
---

The findings, in markdown.
```

## Reviewing

Before committing a document, check that it:

- states findings that will still help someone else, not the story of one conversation
- cites a source for each claim (a web page with its URL, or a benchmark run)
- contains no keys, tokens, user names or local file paths
