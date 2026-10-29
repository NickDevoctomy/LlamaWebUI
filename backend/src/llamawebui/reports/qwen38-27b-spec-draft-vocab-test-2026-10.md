---
title: 'Experimental Qwen3.8-27B spec-draft vocabulary cap: extended interleaved comparison'
slug: qwen38-27b-spec-draft-vocab-test-2026-10
summary: Extended the custom b11815 specvocab comparison to eight completed runs per setting by combining
  the earlier three matching p-min 0.3 samples with five new interleaved runs. Against custom option-off,
  65,536 and 98,304 show higher decode means, but their observed ranges overlap each other and the reference;
  the mean advantage of 98,304 over 65,536 is smaller than either setting’s observed run-to-run range
  width.
tags:
- qwen3.8
- speculative-decoding
- mtp
- spec-draft-vocab
- experimental-build
- rtx4090
- benchmark
updated: '2026-10-29'
author:
  model: openai/gpt-6-luna
  conversation: d40f865f-fec5-4a55-99aa-b137485d5ec5
created: '2026-10-29'
subject:
  profile: qwen3.8-27b-optimise
  model: Qwen3.8-27B-UD-Q4_K_M
hardware:
- Intel Core i7-14700F
- NVIDIA GeForce RTX 4090 24 GB
- 127.8 GiB system memory
runtime: custom b11815-cuda-specvocab build
benchmark: Tetris no details, reasoning medium
outcome: unchanged
results:
- metric: option-off decode mean (8 runs)
  after: 113.115
  unit: tok/s
- metric: N=65536 decode mean (8 runs)
  before: 113.115
  after: 123.75375
  unit: tok/s
- metric: N=65536 decode change vs option off
  after: 9.41
  unit: '%'
- metric: N=98304 decode mean (8 runs)
  before: 113.115
  after: 132.42875
  unit: tok/s
- metric: N=98304 decode change vs option off
  after: 17.07
  unit: '%'
alternatives:
- name: N=65536 vs N=98304 distribution overlap
  summary: 'N=98304 mean exceeds N=65536 by 8.68 tok/s (7.0%); the observed ranges overlap from 121.94
    to 132.55 tok/s. This mean difference is smaller than each group''s run-to-run range width. Prefill
    means: option off 1,996.61 tok/s, N=65536 1,940.51 tok/s, N=98304 2,094.29 tok/s.'
  results:
  - metric: N=65536 mean
    after: 123.75
    unit: tok/s
  - metric: N=98304 mean
    after: 132.43
    unit: tok/s
  - metric: mean gap
    after: 8.68
    unit: tok/s
  - metric: option-off prefill mean
    after: 1996.61
    unit: tok/s
  - metric: N=65536 prefill mean
    after: 1940.51
    unit: tok/s
  - metric: N=98304 prefill mean
    after: 2094.29
    unit: tok/s
left_behind:
- No new profiles, downloads or runtimes.
- Restored qwen3.8-27b-optimise from the original snapshot; custom runtime, N=65536, p-min 0.2 and all
  other starting settings retained. Router is stopped as it was before the follow-up.
---

## Follow-up setup and method

Used `qwen3.8-27b-optimise` on the custom b11815-cuda-specvocab runtime, Tetris no details at reasoning medium, p-min 0.3 and otherwise unchanged launch settings. Interleaved five new runs exactly as requested: option off, N=65536, N=98304, repeated five times. Each setting’s aggregate below includes the three earlier p-min 0.3 runs from the prior report, all on the same custom runtime and matching launch configuration, for eight samples per setting. The official-release samples and the separate p-min 0.2 run are excluded from this comparison.

## Decode results (eight runs per setting)

| Setting | Decode mean | Min–max | Mean change vs custom option off | Run outcome |
|---|---:|---:|---:|---|
| Option off | **113.12 tok/s** | 106.60–120.63 tok/s | Reference | 8/8 completed |
| `--spec-draft-vocab 65536` | **123.75 tok/s** | 113.48–132.55 tok/s | **+9.41%** | 8/8 completed |
| `--spec-draft-vocab 98304` | **132.43 tok/s** | 121.94–163.84 tok/s | **+17.07%** | 8/8 completed |

```chart
{"type":"bar","title":"Custom specvocab decode mean (8 runs each)","categories":["Option off","N=65536","N=98304"],"series":[{"name":"decode mean (tok/s)","values":[113.115,123.75375,132.42875]}],"y_label":"tokens/s","targets":[{"label":"option-off mean","value":113.115}]}
```

New runs in the requested interleaved order:

| Round | Option off | N=65536 | N=98304 |
|---|---:|---:|---:|
| 1 | 111.99 (`032d30bf`) | 132.44 (`e4229ceb`) | 121.94 (`f12e2604`) |
| 2 | 116.59 (`ee31d055`) | 113.58 (`357b48d9`) | 126.00 (`0f2d6408`) |
| 3 | 106.86 (`7a845bad`) | 132.55 (`0f4a0f7d`) | 163.84 (`ead5e620`) |
| 4 | 114.13 (`5079a665`) | 121.61 (`8bdfc091`) | 130.57 (`77700d2d`) |
| 5 | 106.60 (`49a43f4c`) | 131.26 (`9438ddd7`) | 129.93 (`edab5287`) |

Earlier three-run IDs included in the eight-run groups: option off `a867674b`, `4fcdfee4`, `e6fae503`; N=65536 `4d7c2706`, `3280d964`, `d72fabfb`; N=98304 `6332c376`, `7417a192`, `cb41da3f`.

## Does 98k beat 64k by more than the run-to-run spread?

N=98304’s mean is **8.68 tok/s (7.0%)** above N=65536. But the ranges overlap substantially: 65536 spans 113.48–132.55 tok/s, while 98304 spans 121.94–163.84 tok/s; their overlap is 121.94–132.55 tok/s. The 8.68 tok/s difference between means is smaller than either observed range width (19.07 tok/s for 65536 and 41.90 tok/s for 98304). Thus **the data do not show 98k beating 64k by more than the run-to-run spread**. N=98304 has the higher mean, but it is not a decisive separation; its 163.84 tok/s sample notably increases the spread.

Both caps have higher decode means than option-off (+9.41% at 64k and +17.07% at 98k), though their ranges overlap the option-off range (106.60–120.63 tok/s). Prefill means across the same eight runs were 1,996.61 tok/s option off, 1,940.51 tok/s at 65536, and 2,094.29 tok/s at 98304. Peak VRAM was effectively unchanged around 19.63–19.66 GiB; peak RAM was around 6.11–6.25 GiB.

The five new option-off runs plus the three earlier ones give 113.12 tok/s mean. The prior report’s official-release reference mean was 111.73 tok/s, about 1.2% lower; the follow-up compares caps only against option-off on this custom build, as requested.

## Completion, conclusion, and restored state

All five new runs for every setting and all three earlier p-min 0.3 runs per setting completed. **No runs looped or failed to complete.**

The eight-run average favors N=98304 (+17.07% vs option off) over N=65536 (+9.41%), but the means are not separated beyond the observed run-to-run variation. The result supports a higher observed mean for 98k, not a firm conclusion that 98k is reliably faster than 64k on this variable agentic benchmark. The profile has been restored from the original snapshot, including the custom runtime, N=65536 and p-min 0.2. Router remains stopped as it was at the start.
