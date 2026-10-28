# Reports

Each report records one piece of optimisation work by LlamaWebUI's assistant: the profile or model, the starting point, every change tried with its measured result, the final settings and what to try next. Later conversations read them so they don't repeat work, and the ones shipped here give every install a head start.

Research documents (`../research/`) are different: they hold general knowledge with sources. A report is the record of one run of work.

## How reports get here

1. The assistant writes a report to `data/reports/` when it finishes (the `write_report` tool).
2. In the app, **Assistant > Reports** lists them. A user with `settings.write`, on the machine LlamaWebUI runs on from a source checkout, can publish them, which moves them into this folder. With **Auto-publish** on (Settings > Assistant), new reports are written here directly.
3. The maintainer reviews the files here and commits the ones worth shipping.

## Format

Markdown with YAML frontmatter, checked when a report is read or written. Files that don't pass are skipped; this README has no frontmatter, so it is skipped.

```yaml
---
title: Tuning Qwen3.8 27B on an RTX 4090
slug: qwen3-8-27b-rtx4090-tuning      # lowercase words and hyphens; the file is <slug>.md
summary: MTP draft depth 4 and spec-draft-p-min 0.1 raised decode from 112 to 123 tok/s.
tags: [qwen, speculative-decoding]
created: 2026-10-26
updated: 2026-10-26
subject: {profile: qwen3.8-27b, model: unsloth/Qwen3.8-27B-GGUF}
hardware: [RTX 4090 24 GB, Core i7-14700F]
runtime: b11815 cuda
benchmark: Tetris no details, reasoning medium
outcome: improved                    # improved, unchanged, worse, failed or incomplete
results:
  - {metric: decode, before: 112.2, after: 123.1, unit: tok/s}
  - {metric: prefill, before: 1851, after: 2126, unit: tok/s}
author: {model: anthropic/claude-sonnet-5.5, conversation: 6d8372bd-e90c-4d8f-b68f-1ffc75cbbdef}
---
```

Charts are fenced `chart` blocks of JSON in the body. Each series has one value (or `null`) per category:

````markdown
```chart
{"type": "line", "title": "Decode speed per change", "categories": ["baseline", "n-max 16", "n-max 4"],
 "series": [{"name": "decode", "values": [112.2, 102.5, 120.6]}], "y_label": "tok/s",
 "targets": [{"label": "target", "value": 115}]}
```
````

## Reviewing

Before committing a report, check that it:

- names the hardware, runtime and benchmark, so the numbers mean something elsewhere
- gives measured results, not guesses
- contains no keys, tokens, user names or local file paths
