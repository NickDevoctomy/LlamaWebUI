# `llama-server` command-line reference — v0.4.1

## What this document contains

This document is a categorized reference for the `llama-server` command-line arguments and records whether each argument is currently supported by LlamaWebUI. It is intended to make runtime/profile configuration gaps visible and to provide a repeatable baseline for future versioned references.

**Referenced version:** `llama.cpp` release **v0.4.1** (the latest stable release identified on 2026-09-21). The argument descriptions are taken from the upstream `tools/server/README.md` command-line reference available on that date. `llama-server` is build-dependent: an installed executable may expose fewer or additional options, so LlamaWebUI probes `--help` and validates profile options against the selected executable.

**Support marker:** `X` means LlamaWebUI has a first-class launch/profile/configuration surface for the argument. A blank cell means the argument is not a first-class setting in the app. The app's advanced-option escape hatch can pass some runtime-advertised options, but that is deliberately not counted as first-class support. Arguments that are API request fields rather than process arguments are outside this document.

**Effects:** Effects are directional and workload-dependent. `VRAM` means accelerator memory, `RAM` host memory, `load` startup latency, `prefill` prompt processing, `decode` generated-token processing, and `throughput` aggregate request/token capacity.

## Tag reference

| Tag | Definition |
|---|---|
| `backend` | Backend-specific behavior or compatibility. |
| `concurrency` | Number of simultaneous requests, slots, or models. |
| `cpu` | CPU placement, CPU utilization, or CPU scheduling. |
| `decodespeed` | Speed or cost of generating output tokens. |
| `diagnostics` | Inspection, logging, metrics, or troubleshooting. |
| `discovery` | Finding, selecting, listing, or identifying models/runtimes. |
| `disk` | Disk usage, disk I/O, or persistent cache/files. |
| `loadspeed` | Model or runtime startup/load latency. |
| `network` | Network binding, transport, download, or remote execution. |
| `prefillspeed` | Speed or cost of processing input/prompt tokens. |
| `quality` | Model output quality, numerical precision, or behavior fidelity. |
| `ram` | Host memory consumption or residency. |
| `reliability` | Startup success, allocation safety, or operational resilience. |
| `security` | Authentication, authorization, isolation, CORS, TLS, or secret exposure. |
| `throughput` | Aggregate tokens/requests processed over time. |
| `usability` | User-facing workflow, configuration, or interaction behavior. |
| `vram` | Device memory consumption or GPU/accelerator placement. |

## Common, process, and CPU parameters

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--help` | `-h`, `--usage` | Print usage and exit. | Diagnostics only. | `diagnostics`, `usability` | |
| `--version` | — | Show version and build information. | No inference effect; identifies compatibility. | `discovery`, `diagnostics` | X |
| `--cache-list` | `-cl` | List models in the llama.cpp cache. | Avoids network/download work; no inference effect. | `discovery`, `disk` | |
| `--completion-bash` | — | Print a Bash completion script. | Shell usability only. | `usability` | |
| `--threads N` | `-t` | Number of CPU threads used during generation. | Changes CPU decode speed and CPU utilization. | `cpu`, `decodespeed`, `throughput` | X |
| `--threads-batch N` | `-tb` | CPU threads used for batch/prompt processing. | Primarily changes prefill speed and CPU utilization. | `cpu`, `prefillspeed`, `throughput` | |
| `--cpu-mask M` | `-C` | CPU affinity mask for generation. | Can improve CPU locality or reserve cores. | `cpu`, `decodespeed`, `reliability` | |
| `--cpu-range lo-hi` | `-Cr` | CPU affinity range for generation. | Same as CPU mask, using a range. | `cpu`, `decodespeed`, `reliability` | |
| `--cpu-strict 0\|1` | — | Require strict CPU placement. | May improve locality; can reduce startup reliability if CPUs are unavailable. | `cpu`, `reliability` | |
| `--prio N` | — | Set generation thread priority. | May improve latency at the expense of other processes. | `cpu`, `decodespeed`, `reliability` | |
| `--poll 0..100` | — | Polling level while waiting for work. | Trades CPU use for response latency. | `cpu`, `decodespeed`, `throughput` | |
| `--cpu-mask-batch M` | `-Cb` | CPU affinity mask for batch/prompt work. | Affects prefill locality and CPU use. | `cpu`, `prefillspeed` | |
| `--cpu-range-batch lo-hi` | `-Crb` | CPU affinity range for batch/prompt work. | Affects prefill locality and CPU use. | `cpu`, `prefillspeed` | |
| `--cpu-strict-batch 0\|1` | — | Require strict placement for batch work. | Can improve locality; may reduce resilience. | `cpu`, `reliability` | |
| `--prio-batch N` | — | Set batch thread priority. | Can improve prompt latency at the expense of other work. | `cpu`, `prefillspeed` | |
| `--poll-batch 0\|1` | — | Poll while waiting for batch work. | Trades CPU use for prompt latency. | `cpu`, `prefillspeed` | |
| `--numa TYPE` | — | NUMA optimization: `distribute`, `isolate`, or `numactl`. | Can improve CPU/RAM locality on multi-node hosts. | `cpu`, `ram`, `prefillspeed`, `decodespeed` | |

## Context, batching, and KV-cache parameters

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--ctx-size N` | `-c` | Prompt context size; `0` loads the model value. | Higher values increase KV-cache VRAM/RAM and can reduce concurrency; enables longer prompts. | `vram`, `ram`, `concurrency`, `quality`, `usability` | X |
| `--predict N` | `-n`, `--n-predict` | Maximum output tokens; `-1` means unlimited. | Limits decode work and request duration, not model quality directly. | `decodespeed`, `throughput`, `usability` | |
| `--batch-size N` | `-b` | Logical maximum batch size. | Larger values can improve prompt throughput but consume more memory. | `vram`, `ram`, `prefillspeed`, `throughput` | X |
| `--ubatch-size N` | `-ub` | Physical maximum batch size. | Larger values can improve prefill throughput but increase peak memory. | `vram`, `ram`, `prefillspeed`, `throughput` | X |
| `--keep N` | — | Tokens retained when context shifting; `-1` keeps all. | Changes long-conversation behavior and context reuse. | `quality`, `usability`, `ram` | |
| `--swa-full` | — | Use a full-size sliding-window-attention cache. | Increases KV memory; may preserve quality for SWA models. | `vram`, `ram`, `quality` | |
| `--kv-offload` | `-kvo`, `--no-kv-offload` | Enable or disable KV-cache offloading. | Offloading can reduce host memory but consumes VRAM; disabling can do the reverse. | `vram`, `ram`, `backend` | |
| `--kv-unified` | `-kvu`, `--no-kv-unified` | Use one KV buffer shared by sequences. | Can improve memory efficiency and concurrency; affects allocation behavior. | `vram`, `ram`, `concurrency`, `reliability` | |
| `--kv-unified-per-slot N` | — | Context limit per parallel slot. | Controls per-request memory when using a shared KV pool. | `vram`, `ram`, `concurrency` | |
| `--cache-ram N` | `-cram` | Maximum prompt-cache size in MiB; `0` disables and `-1` removes the limit. | More RAM/disk-backed cache can improve repeated-prompt prefill speed. | `ram`, `disk`, `prefillspeed` | X |
| `--cache-idle-slots` | `--no-cache-idle-slots` | Save/clear idle slots in unified KV mode. | Trades RAM/cache reuse against idle memory. | `ram`, `concurrency`, `prefillspeed` | |
| `--cache-prompt` | `--no-cache-prompt` | Enable prompt/KV prefix caching. | Can substantially improve repeated-prefix prefill speed; uses cache memory. | `prefillspeed`, `ram`, `throughput` | |
| `--cache-reuse N` | — | Minimum reusable chunk size for KV shifting. | Can improve prefill speed for related prompts; uses cache state. | `prefillspeed`, `ram` | |
| `--context-shift` | `--no-context-shift` | Shift context for effectively unlimited generation. | Preserves long-running usability at possible quality/context cost. | `quality`, `usability`, `reliability` | |
| `--ctx-checkpoints N` | `-ctxcp`, `--swa-checkpoints` | Maximum context checkpoints per slot. | Trades memory/disk state for context-management flexibility. | `ram`, `concurrency`, `reliability` | |
| `--checkpoint-min-step N` | `-cms` | Minimum token spacing between checkpoints. | Reduces checkpoint work; affects long-context responsiveness. | `prefillspeed`, `ram`, `disk` | |
| `--defrag-thold N` | `-dt` | KV-cache defragmentation threshold (deprecated). | Can affect latency and memory fragmentation on older builds. | `vram`, `ram`, `reliability` | |

## Attention, RoPE, and numerical behavior

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--flash-attn on\|off\|auto` | `-fa` | Select Flash Attention mode. | Often improves prefill speed and lowers attention memory, subject to backend support. | `backend`, `vram`, `prefillspeed`, `decodespeed` | X |
| `--rope-scaling TYPE` | `none`, `linear`, `yarn` | Select RoPE context scaling. | Changes usable context and output fidelity; may change compute cost. | `quality`, `vram`, `prefillspeed` | |
| `--rope-scale N` | — | RoPE context scaling factor. | Expands context at possible quality and compute cost. | `quality`, `prefillspeed`, `decodespeed` | |
| `--rope-freq-base N` | — | RoPE base frequency. | Changes positional behavior and long-context quality. | `quality` | |
| `--rope-freq-scale N` | — | RoPE frequency scale. | Changes positional behavior and effective context. | `quality`, `prefillspeed` | |
| `--yarn-orig-ctx N` | — | Original model context for YaRN. | Affects long-context scaling and output fidelity. | `quality`, `prefillspeed` | |
| `--yarn-ext-factor N` | — | YaRN interpolation/extrapolation mix. | Trades long-context extension against fidelity. | `quality` | |
| `--yarn-attn-factor N` | — | YaRN attention magnitude scaling. | Changes long-context numerical behavior. | `quality` | |
| `--yarn-beta-slow N` | — | YaRN high-correction dimension/alpha. | Changes long-context numerical behavior. | `quality` | |
| `--yarn-beta-fast N` | — | YaRN low-correction dimension/beta. | Changes long-context numerical behavior. | `quality` | |
| `--repack` | `-nr`, `--no-repack` | Enable weight repacking. | May improve decode speed; can change load time and backend compatibility. | `backend`, `loadspeed`, `decodespeed` | |
| `--no-host` | — | Bypass host buffers where possible. | Can reduce RAM but may require more device buffers and affect compatibility. | `ram`, `vram`, `backend`, `reliability` | |
| `--op-offload` | `--no-op-offload` | Offload host tensor operations to a device. | Can improve speed and increase VRAM use; backend-dependent. | `backend`, `vram`, `prefillspeed`, `decodespeed` | |
| `--check-tensors` | — | Check model tensor data for invalid values. | Increases load time; improves diagnostics and startup reliability. | `diagnostics`, `loadspeed`, `reliability` | |

## Device placement, MoE, and fit-to-memory

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--device DEVICES` | `-dev` | Comma-separated devices for offloading; `none` disables offload. | Controls VRAM use, speed, and backend selection. | `backend`, `vram`, `decodespeed`, `prefillspeed` | |
| `--list-devices` | — | List available accelerator devices and exit. | Discovery/diagnostics only. | `discovery`, `diagnostics`, `backend` | X |
| `--override-tensor PATTERN=TYPE` | `-ot` | Override tensor buffer types. | Can trade VRAM/RAM, speed, and quality at tensor granularity. | `backend`, `vram`, `ram`, `quality` | X |
| `--cpu-moe` | `-cmoe` | Keep all MoE weights on CPU. | Reduces VRAM but usually reduces decode speed and increases host traffic. | `vram`, `ram`, `cpu`, `decodespeed` | |
| `--n-cpu-moe N` | `-ncmoe` | Keep first N layers of MoE weights on CPU. | Partial VRAM reduction with a speed trade-off. | `vram`, `ram`, `cpu`, `decodespeed` | |
| `--n-cpu-ffn N` | `-ncffn` | Keep first N dense FFN layers on CPU. | Partial VRAM reduction with a speed trade-off. | `vram`, `ram`, `cpu`, `decodespeed` | |
| `--n-gpu-layers N` | `-ngl`, `--gpu-layers` | Maximum model layers stored in VRAM; accepts a number, `auto`, or `all`. | Primary VRAM/speed placement control; more layers generally improve speed. | `vram`, `ram`, `decodespeed`, `prefillspeed`, `backend` | X |
| `--split-mode MODE` | `-sm` | Multi-GPU split: `none`, `layer`, `row`, or `tensor`. | Controls VRAM distribution and parallelism; experimental modes affect reliability. | `vram`, `backend`, `throughput`, `reliability` | |
| `--tensor-split N0,N1,...` | `-ts` | Per-GPU model split proportions. | Balances VRAM use and multi-GPU throughput. | `vram`, `backend`, `throughput` | |
| `--main-gpu INDEX` | `-mg` | Main GPU for single-GPU or row-split operation. | Changes placement and device traffic. | `vram`, `backend`, `throughput` | |
| `--fit on\|off` | `-fit` | Adjust unset settings to fit device memory. | Improves allocation reliability; may lower context or placement choices. | `vram`, `reliability`, `usability` | X |
| `--fit-target MiB,...` | `-fitt` | Per-device memory margin used by `--fit`. | Improves allocation safety at the cost of usable VRAM. | `vram`, `reliability` | |
| `--fit-ctx N` | — | Minimum context allowed by `--fit`. | Sets the quality/usability floor during automatic fitting. | `vram`, `reliability`, `usability` | |

## Model sources, loading, adapters, and multimodal options

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--model FNAME` | `-m` | Local model path to load. | Selects model and determines disk/RAM/VRAM footprint. | `discovery`, `disk`, `loadspeed`, `usability` | X |
| `--model-url URL` | `-mu` | Download model from a URL. | Adds network and disk work before loading. | `network`, `disk`, `loadspeed` | |
| `--docker-repo REPO[:QUANT]` | `-dr` | Select a Docker Hub model. | Adds remote model discovery/download behavior. | `network`, `discovery`, `disk` | |
| `--hf-repo REPO[:QUANT]` | `-hf`, `-hfr` | Select a Hugging Face model/revision quantization. | Controls remote discovery and download; may download mmproj. | `network`, `discovery`, `disk`, `security` | |
| `--hf-file FILE` | `-hff` | Select a file within the Hugging Face repository. | Controls downloaded artifact and disk usage. | `network`, `discovery`, `disk` | |
| `--hf-token TOKEN` | `-hft` | Hugging Face access token. | Enables private/gated downloads; secret exposure risk. | `network`, `security` | |
| `--load-mode MODE` | `-lm` | Loading mode: `auto`, `none`, `mmap`, `mlock`, `mmap+mlock`, or `dio`. | Changes load latency, RAM residency, paging, and disk I/O. | `loadspeed`, `ram`, `disk`, `reliability` | X |
| `--lazy-mode MODE` | `-lzm` | On-demand reading of selected large tensors. | Can reduce resident RAM and startup work, with possible disk I/O during inference. | `ram`, `disk`, `loadspeed`, `decodespeed` | X |
| `--rpc SERVERS` | — | Use comma-separated remote RPC servers. | Moves work/network traffic remotely; backend and latency dependent. | `network`, `backend`, `vram`, `decodespeed` | |
| `--lora FNAME` | — | Load one or more LoRA adapters. | Changes quality/behavior and adds load/memory work. | `quality`, `vram`, `ram`, `loadspeed` | |
| `--lora-scaled FNAME:SCALE` | — | Load LoRA adapters with explicit scales. | Controls adapter effect and memory/load cost. | `quality`, `vram`, `loadspeed` | |
| `--lora-init-without-apply` | — | Load LoRAs initially disabled. | Improves workflow flexibility; adapters still consume load/memory resources. | `quality`, `usability`, `ram` | |
| `--control-vector FNAME` | — | Load control vectors. | Alters output behavior and adds model data. | `quality`, `ram`, `loadspeed` | |
| `--control-vector-scaled FNAME:SCALE` | — | Load control vectors with scales. | Controls behavior effect. | `quality`, `ram` | |
| `--control-vector-layer-range START END` | — | Restrict control-vector layers. | Changes behavior and compute impact. | `quality`, `decodespeed` | |
| `--mmproj FILE` | `-mm` | Path to a multimodal projector. | Enables vision/audio input and consumes RAM/VRAM/load time. | `backend`, `vram`, `ram`, `quality` | |
| `--mmproj-url URL` | `-mmu` | Download a multimodal projector. | Adds network/disk/load work. | `network`, `disk`, `loadspeed` | |
| `--mmproj-auto` | `--no-mmproj`, `--no-mmproj-auto` | Automatically use an available projector. | Changes multimodal discovery and startup behavior. | `discovery`, `backend`, `loadspeed` | |
| `--mmproj-offload` | `--no-mmproj-offload` | Enable projector GPU offload. | Trades VRAM for multimodal speed. | `vram`, `prefillspeed`, `backend` | |
| `--mmproj-device DEVICE` | `-mmdev` | Device for projector offload. | Controls multimodal VRAM and speed. | `vram`, `backend`, `prefillspeed` | |
| `--image-min-tokens N` | — | Minimum tokens per image for dynamic resolution. | Affects image quality, context use, and prefill cost. | `quality`, `prefillspeed`, `vram` | |
| `--image-max-tokens N` | — | Maximum tokens per image for dynamic resolution. | Higher values can improve image detail but consume context/memory. | `quality`, `prefillspeed`, `vram` | |
| `--mtmd-batch-max-tokens N` | — | Maximum image tokens per encoding batch. | Trades multimodal prefill speed against memory. | `prefillspeed`, `vram`, `throughput` | |
| `--video-fps N` | — | Target video frame rate. | Changes multimodal input volume and prefill cost. | `prefillspeed`, `quality`, `throughput` | |
| `--video-timestamp-interval N` | — | Interval between video timestamps. | Changes input detail and tokenization work. | `prefillspeed`, `quality` | |
| `--video-ffmpeg-dir DIR` | — | Directory containing ffmpeg/ffprobe. | Enables video processing; affects reliability and usability. | `backend`, `reliability`, `usability` | |

## Sampling and output behavior

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--samplers SAMPLERS` | — | Ordered sampler chain separated by `;`. | Changes output quality/behavior and decode cost. | `quality`, `decodespeed` | |
| `--seed SEED` | `-s` | RNG seed; `-1` selects a random seed. | Controls reproducibility and output variation. | `quality`, `reliability` | |
| `--sampler-seq SEQUENCE` | `--sampling-seq` | Compact sampler sequence. | Changes sampling behavior. | `quality` | |
| `--ignore-eos` | — | Ignore end-of-stream token. | Allows continued generation; can increase decode time/output length. | `quality`, `decodespeed`, `usability` | |
| `--temperature N` | `--temp` | Sampling temperature. | Higher values increase variation; sampling cost is small. | `quality` | |
| `--top-k N` | — | Limit sampling to K likely tokens. | Changes quality/variation and minor decode cost. | `quality`, `decodespeed` | |
| `--top-p N` | — | Nucleus sampling probability threshold. | Changes quality/variation and minor decode cost. | `quality`, `decodespeed` | |
| `--min-p N` | — | Relative minimum probability threshold. | Changes quality/variation and minor decode cost. | `quality`, `decodespeed` | |
| `--top-n-sigma N` | `--top-nsigma` | Sigma-based sampling threshold. | Changes output distribution. | `quality` | |
| `--xtc-probability N` | — | Probability of XTC token removal. | Changes repetition/variation and small decode cost. | `quality`, `decodespeed` | |
| `--xtc-threshold N` | — | XTC probability threshold. | Changes output distribution. | `quality` | |
| `--typical-p N` | `--typical` | Locally typical sampling parameter. | Changes output distribution. | `quality` | |
| `--repeat-last-n N` | — | Number of recent tokens considered for repetition penalties. | Changes quality and small memory/compute cost. | `quality`, `decodespeed` | |
| `--repeat-penalty N` | — | Repetition penalty. | Reduces repeated text; may affect fidelity. | `quality` | |
| `--presence-penalty N` | — | Presence penalty. | Encourages novel tokens/topics. | `quality` | |
| `--frequency-penalty N` | — | Frequency penalty. | Reduces frequent-token repetition. | `quality` | |
| `--dry-multiplier N` | — | DRY repetition penalty multiplier. | Changes repetition behavior and small decode cost. | `quality`, `decodespeed` | |
| `--dry-base N` | — | DRY penalty base. | Changes repetition behavior. | `quality` | |
| `--dry-allowed-length N` | — | DRY repetition length exempt from penalty. | Changes output behavior. | `quality` | |
| `--dry-penalty-last-n N` | — | Number of tokens scanned by DRY. | Changes quality and small decode cost. | `quality`, `decodespeed` | |
| `--dry-sequence-breaker STRING` | — | Replace DRY sequence breakers. | Changes repetition boundaries. | `quality` | |
| `--adaptive-target N` | — | Adaptive-p target probability. | Changes output distribution and sampling stability. | `quality` | |
| `--adaptive-decay N` | — | Adaptive-p target adaptation rate. | Changes output distribution and stability. | `quality` | |
| `--dynatemp-range N` | — | Dynamic temperature range. | Changes variation during generation. | `quality` | |
| `--dynatemp-exp N` | — | Dynamic temperature exponent. | Changes variation during generation. | `quality` | |
| `--mirostat N` | — | Enable Mirostat mode 1 or 2. | Controls perplexity/output stability with extra sampling work. | `quality`, `decodespeed` | |
| `--mirostat-lr N` | `--mirostat-eta` | Mirostat learning rate. | Changes sampling stability. | `quality` | |
| `--mirostat-ent N` | `--mirostat-tau` | Mirostat target entropy. | Changes sampling stability. | `quality` | |
| `--logit-bias TOKEN_ID(+/-)BIAS` | `-l` | Modify likelihood of specific tokens. | Can force or suppress content; may affect quality. | `quality` | |
| `--grammar GRAMMAR` | — | Inline grammar constraint. | Constrains output quality/shape; adds decode work. | `quality`, `decodespeed` | |
| `--grammar-file FNAME` | — | Read a grammar from a file. | Same as grammar; adds disk configuration. | `quality`, `disk` | |
| `--json-schema SCHEMA` | `-j` | Constrain output to a JSON schema. | Improves structural reliability; adds decode work. | `quality`, `reliability`, `decodespeed` | |
| `--json-schema-file FILE` | `-jf` | Read a JSON schema from a file. | Same as JSON schema; adds disk configuration. | `quality`, `reliability`, `disk` | |
| `--backend-sampling` | `-bs` | Enable experimental backend sampling. | May improve speed; backend compatibility varies. | `backend`, `decodespeed`, `reliability` | |

## Server, routing, API, security, and observability parameters

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--alias STRING` | `-a` | API model alias, comma-separated. | Controls client-facing model identity and routing usability. | `usability`, `discovery` | X |
| `--tags STRING` | — | Informational model tags. | Improves identification only. | `discovery`, `usability` | |
| `--host HOST` | — | Listen address or Unix socket path. | Controls reachability and exposure. | `network`, `security` | X |
| `--port PORT` | — | Listen port. | Controls endpoint location and conflicts. | `network`, `reliability` | X |
| `--reuse-port` | — | Allow multiple sockets on one port. | Can support parallel instances; increases operational risk. | `network`, `reliability` | |
| `--path PATH` | — | Static-file directory. | Changes UI/static asset serving and disk exposure. | `network`, `security`, `disk` | |
| `--cors-origins ORIGINS` | — | Allowed CORS origins. | Controls browser access and cross-origin exposure. | `security`, `network` | |
| `--cors-methods METHODS` | — | Allowed CORS methods. | Controls cross-origin API surface. | `security`, `network` | |
| `--cors-headers HEADERS` | — | Allowed CORS headers. | Controls cross-origin request surface. | `security`, `network` | |
| `--cors-credentials` | `--no-cors-credentials` | Allow credentials in CORS requests. | Affects browser security and authentication exposure. | `security`, `network` | |
| `--api-prefix PREFIX` | — | Prefix served API paths. | Affects reverse-proxy integration and usability. | `network`, `usability` | |
| `--api-key KEY` | — | Inline API key(s). | Enables authentication but risks secret exposure in process listings/configuration. | `security` | |
| `--api-key-file FNAME` | — | File containing one API key per line. | Enables authentication with less command-line exposure; file remains sensitive. | `security`, `reliability` | X |
| `--ssl-key-file FNAME` | — | PEM private-key file. | Enables TLS; protects transport but adds deployment requirements. | `security`, `network`, `reliability` | |
| `--ssl-cert-file FNAME` | — | PEM certificate file. | Enables TLS. | `security`, `network`, `reliability` | |
| `--timeout N` | `-to` | HTTP read/write timeout in seconds. | Controls long-request reliability and resource occupancy. | `network`, `reliability`, `concurrency` | |
| `--sse-ping-interval N` | — | Interval for SSE keepalive comments; `-1` disables. | Keeps long streams observable and prevents intermediary timeouts. | `network`, `reliability`, `usability` | |
| `--threads-http N` | — | HTTP request-processing threads. | Changes request concurrency and control-plane latency. | `cpu`, `concurrency`, `throughput` | |
| `--parallel N` | `-np` | Number of server slots; `-1` means auto. | Controls concurrency, aggregate throughput, and KV memory. | `concurrency`, `throughput`, `vram`, `ram` | |
| `--cont-batching` | `--no-cont-batching` | Enable continuous/dynamic batching. | Usually improves aggregate throughput; changes latency and memory behavior. | `concurrency`, `throughput`, `prefillspeed`, `vram` | |
| `--models-dir PATH` | — | Directory containing router models. | Controls model discovery and disk access. | `discovery`, `disk`, `usability` | |
| `--models-preset PATH` | — | INI file containing router model presets. | Defines durable per-model launch settings and routing inventory. | `discovery`, `usability`, `reliability` | X |
| `--models-max N` | — | Maximum simultaneously loaded router models. | Bounds VRAM/RAM and concurrency. | `concurrency`, `vram`, `ram`, `reliability` | |
| `--models-autoload` | `--no-models-autoload` | Automatically load router models. | Trades first-request latency against resource use. | `loadspeed`, `vram`, `ram`, `usability` | |
| `--media-path PATH` | — | Root directory for local media files. | Enables media input but increases file exposure. | `security`, `disk`, `usability` | |
| `--embedding` | `--embeddings` | Restrict server to embedding use. | Changes endpoint behavior and model-purpose compatibility. | `quality`, `usability`, `backend` | |
| `--rerank` | `--reranking` | Enable reranking endpoint. | Changes endpoint behavior and model-purpose compatibility. | `quality`, `usability`, `backend` | |
| `--metrics` | — | Enable Prometheus metrics endpoint. | Adds observability with possible information exposure. | `diagnostics`, `throughput`, `security` | |
| `--props` | — | Allow changing global properties via API. | Adds runtime configurability and attack surface. | `usability`, `security` | |
| `--slots` | `--no-slots` | Expose slot monitoring endpoint. | Adds observability and possible state exposure. | `diagnostics`, `concurrency`, `security` | |
| `--slot-save-path PATH` | — | Directory for saved slot KV caches. | Adds disk use and can improve resumability. | `disk`, `ram`, `prefillspeed` | |
| `--jinja` | `--no-jinja` | Enable Jinja chat templates. | Affects formatting, tool calls, reasoning, and output quality. | `quality`, `backend`, `usability` | |
| `--reasoning FORMAT` | `-rea` | Reasoning mode: `on`, `off`, or `auto`. | Changes output behavior and generated-token cost. | `quality`, `decodespeed`, `usability` | |
| `--reasoning-format FORMAT` | — | Parse/expose reasoning as none, DeepSeek, or legacy. | Changes response shape and client compatibility. | `quality`, `usability` | |
| `--reasoning-effort LEVEL` | — | Template reasoning effort level. | Changes output quality and decode cost. | `quality`, `decodespeed` | |
| `--reasoning-budget N` | — | Token budget for thinking. | Bounds decode time and output allocation. | `decodespeed`, `throughput`, `usability` | |
| `--reasoning-budget-message MESSAGE` | — | Message injected when thinking budget is exhausted. | Changes output behavior. | `quality`, `usability` | |
| `--reasoning-preserve` | `--no-reasoning-preserve` | Preserve reasoning in full history. | Changes multi-turn quality and context consumption. | `quality`, `ram`, `usability` | X |
| `--chat-template TEMPLATE` | — | Override the model's Jinja chat template. | Can fix compatibility or substantially change output quality. | `quality`, `backend`, `reliability` | |
| `--chat-template-file FILE` | — | Read a custom chat template. | Same as template override; adds disk configuration. | `quality`, `backend`, `disk` | |
| `--chat-template-kwargs JSON` | — | Extra JSON parameters for the template parser. | Changes formatting/reasoning behavior. | `quality`, `usability` | |
| `--skip-chat-parsing` | `--no-skip-chat-parsing` | Force content-only parsing. | Changes reasoning/tool-call handling and compatibility. | `quality`, `backend`, `usability` | |
| `--prefill-assistant` | `--no-prefill-assistant` | Prefill assistant messages when the last message is assistant. | Changes prompt processing and chat behavior. | `prefillspeed`, `quality`, `usability` | |
| `--slot-prompt-similarity N` | `-sps` | Minimum prompt similarity for slot reuse. | Can improve prefill speed; affects scheduling fairness. | `prefillspeed`, `concurrency`, `throughput` | |
| `--sleep-idle-seconds N` | — | Unload model memory after idle time. | Reduces idle VRAM/RAM; increases next-request load latency. | `vram`, `ram`, `loadspeed`, `reliability` | |
| `--warmup` | `--no-warmup` | Run an empty warmup request at startup. | Increases startup time but improves first-request latency/reliability. | `loadspeed`, `reliability` | |
| `--spm-infill` | — | Use Suffix/Prefix/Middle infill ordering. | Changes code-completion compatibility. | `quality`, `usability` | |
| `--pooling TYPE` | — | Embedding pooling type. | Changes embedding quality/shape and compatibility. | `quality`, `backend` | |
| `--embd-normalize N` | — | Embedding normalization mode. | Changes embedding values and retrieval quality. | `quality` | |
| `--ui` | `--webui`, `--no-ui` | Enable or disable the built-in Web UI. | Changes served surface and security exposure. | `usability`, `security`, `network` | |
| `--ui-config JSON` | `--webui-config` | Inline default Web UI settings. | Changes built-in UI behavior. | `usability` | |
| `--ui-config-file PATH` | `--webui-config-file` | File containing default Web UI settings. | Changes built-in UI behavior and adds disk configuration. | `usability`, `disk` | |
| `--agent` | `--no-agent` | Enable CORS proxy and built-in agent tools. | Adds powerful local access and significant security risk. | `security`, `usability`, `network` | |
| `--tools TOOL1,TOOL2,...` | — | Enable built-in tools. | Adds local file/command capabilities and security exposure. | `security`, `usability` | |
| `--tools-runtime OPTION` | — | Run tools in Docker, Podman, SSH, or host runtime. | Provides isolation/remote execution with setup and latency costs. | `security`, `network`, `reliability` | |
| `--mcp-servers-config PATH` | — | Load MCP server definitions from JSON. | Spawns trusted child processes and expands tool surface. | `security`, `network`, `usability` | |
| `--mcp-servers-json JSON` | — | Inline MCP server definitions. | Same as MCP config; risks secret/config exposure. | `security`, `network`, `usability` | |

## Logging, diagnostics, and offline behavior

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--log-disable` | — | Disable logging. | Reduces diagnostics and log I/O. | `diagnostics`, `disk` | |
| `--log-file FNAME` | — | Write logs to a file. | Adds disk I/O and durable diagnostics. | `diagnostics`, `disk` | |
| `--log-jsonl` | `--no-log-jsonl` | Emit JSON Lines logs. | Improves machine parsing; changes log volume/format. | `diagnostics`, `disk`, `usability` | |
| `--log-colors on\|off\|auto` | — | Control colored logs. | Presentation only. | `diagnostics`, `usability` | |
| `--verbose` | `-v`, `--log-verbose` | Enable maximum verbosity. | Increases diagnostics and log I/O; may affect throughput slightly. | `diagnostics`, `disk` | |
| `--verbosity N` | `-lv`, `--log-verbosity` | Set log verbosity threshold. | Controls diagnostic detail and log volume. | `diagnostics`, `disk` | |
| `--log-prefix` | `--no-log-prefix` | Enable log prefixes. | Improves log identification. | `diagnostics`, `usability` | |
| `--log-timestamps` | `--no-log-timestamps` | Enable log timestamps. | Improves troubleshooting and correlation. | `diagnostics`, `usability` | |
| `--perf` | `--no-perf` | Enable internal performance timings. | Adds useful timing diagnostics with minimal overhead. | `diagnostics`, `prefillspeed`, `decodespeed` | |
| `--offline` | — | Use cache only and prevent network access. | Improves reproducibility/security; prevents remote discovery/download. | `network`, `reliability`, `security`, `disk` | |
| `--log-prompts-dir PATH` | — | Log prompts to a directory. | Adds diagnostics and severe sensitive-data exposure risk. | `diagnostics`, `disk`, `security` | |

## Speculative decoding parameters

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--spec-type TYPES` | — | Select speculative decoding types. | Can improve decode speed if acceptance is good; adds complexity. | `decodespeed`, `throughput`, `reliability` | |
| `--spec-draft-model FNAME` | `-md` | Draft model for speculative decoding. | Adds RAM/VRAM/load time; may improve decode speed. | `decodespeed`, `vram`, `ram`, `loadspeed` | |
| `--spec-draft-n-max N` | `--draft-max` | Maximum draft tokens per step. | Trades draft work against decode speed and memory. | `decodespeed`, `throughput` | |
| `--spec-draft-n-min N` | `--draft-min` | Minimum draft tokens. | Changes speculative acceptance and overhead. | `decodespeed`, `quality` | |
| `--spec-draft-p-split P` | `--draft-p-split` | Draft/target split probability. | Changes speculative behavior and speed. | `decodespeed`, `quality` | |
| `--spec-draft-p-min P` | `--draft-p-min` | Minimum speculative probability. | Controls when speculation is used. | `decodespeed`, `reliability` | |
| `--spec-draft-device DEVICES` | `-devd` | Devices for draft-model offload. | Controls draft VRAM and speed. | `vram`, `backend`, `decodespeed` | |
| `--spec-draft-ngl N` | `-ngld` | Draft-model GPU layers. | Controls draft VRAM and speed. | `vram`, `decodespeed` | |
| `--spec-draft-threads N` | `-td` | Draft generation CPU threads. | Changes draft CPU work and latency. | `cpu`, `decodespeed` | |
| `--spec-draft-threads-batch N` | `-tbd` | Draft batch CPU threads. | Changes draft prefill work. | `cpu`, `prefillspeed` | |
| `--spec-draft-cache-type-k TYPE` | `-ctkd` | Draft KV K-cache type. | Trades draft memory, speed, and quality. | `vram`, `ram`, `quality`, `decodespeed` | |
| `--spec-draft-cache-type-v TYPE` | `-ctvd` | Draft KV V-cache type. | Trades draft memory, speed, and quality. | `vram`, `ram`, `quality`, `decodespeed` | |
| `--spec-draft-cpu-moe` | `-cmoed` | Keep draft MoE weights on CPU. | Reduces draft VRAM at speed cost. | `vram`, `ram`, `cpu`, `decodespeed` | |
| `--spec-draft-n-cpu-moe N` | `-ncmoed` | Keep first N draft MoE layers on CPU. | Partial draft VRAM reduction at speed cost. | `vram`, `ram`, `cpu`, `decodespeed` | |
| `--spec-draft-backend-sampling` | `--no-spec-draft-backend-sampling` | Offload draft sampling to backend. | May improve speculative speed; backend-dependent. | `backend`, `decodespeed` | |
| `--spec-synth-len L` | — | Synthetic acceptance length for benchmarks. | Diagnostics/benchmarking only. | `diagnostics`, `decodespeed` | |
| `--spec-synth-rates P0,P1,...` | — | Synthetic acceptance probabilities. | Diagnostics/benchmarking only. | `diagnostics`, `decodespeed` | |
| `--spec-default` | — | Enable the default speculative configuration. | Convenience; may improve decode speed with extra memory/load. | `decodespeed`, `vram`, `loadspeed` | |

## Compatibility and removed arguments

| Argument | Alias / CLI form | Description | Effects | Tags | Supported |
|---|---|---|---|---|---|
| `--draft` | `--draft-n`, `--draft-max` | Removed legacy draft-token argument; use `--spec-draft-n-max` or the relevant n-gram option. | No effect on current builds; retained for migration awareness. | `reliability`, `usability` | |
| `--draft-min` | `--draft-n-min` | Removed legacy minimum draft argument. | No effect on current builds. | `reliability`, `usability` | |
| `--spec-ngram-size-n` | — | Removed legacy n-gram size argument. | No effect on current builds. | `reliability`, `usability` | |
| `--spec-ngram-size-m` | — | Removed legacy n-gram size argument. | No effect on current builds. | `reliability`, `usability` | |
| `--spec-ngram-min-hits` | — | Removed legacy n-gram hit threshold. | No effect on current builds. | `reliability`, `usability` | |

## LlamaWebUI support summary

LlamaWebUI currently provides first-class support for the model path (`--model`), router preset (`--models-preset`), host/port, API-key file, runtime version/device probing, model alias through preset sections, and the profile controls represented by `--n-gpu-layers`, `--ctx-size`, `--flash-attn`, `--load-mode`, `--lazy-mode`, `--cache-ram`, `--fit`, `--override-tensor`, `--cache-type-k`, `--cache-type-v`, `--threads`, `--batch-size`, `--ubatch-size`, and `--no-reasoning-preserve`.

The app also exposes a generic advanced-option field, validated against the selected executable's probed `--help` output. That mechanism is intentionally not marked as first-class support in the tables above. This distinction makes it possible to identify the next explicit UI/API work without confusing pass-through compatibility with a designed workflow.

## Sources and update procedure

- Upstream argument reference: [`ggml-org/llama.cpp/tools/server/README.md`](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), retrieved 2026-09-21.
- Latest stable release page: [`ggml-org/llama.cpp v0.4.1`](https://github.com/ggml-org/llama.cpp/releases/tag/v0.4.1).
- LlamaWebUI support inventory: `backend/src/llamawebui/domain/model_profile.py`, `backend/src/llamawebui/domain/router_lifecycle.py`, `backend/src/llamawebui/services/runtime_probe.py`, and `backend/src/llamawebui/domain/runtime_capabilities.py`.

For a future update, copy this document, change the version in the filename and heading, refresh the upstream argument list, then re-check every `X` against the current app's launch construction, profile model, API request models, and runtime capability probing.
