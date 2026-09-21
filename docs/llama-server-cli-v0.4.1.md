# llama-server CLI reference — v0.4.1

## What this document contains

This is a versioned comparison of the `llama-server` command-line interface and the launch controls currently exposed by LlamaWebUI. It records the option aliases, the upstream meaning, compact effect tags for UI presentation, and whether LlamaWebUI currently supports the option as a first-class launch setting.

An `X` in **LlamaWebUI support** means the application currently emits or controls the option through its generated model preset or fixed router launch arguments. A blank cell means there is no first-class control today. The application's **Advanced options** can preserve and emit additional options when the selected runtime advertises them; that compatibility escape hatch is intentionally not counted as first-class support.

This document describes startup options, not the JSON request parameters accepted by `/v1/chat/completions`, `/v1/completions`, `/completion`, or other HTTP endpoints. Startup options generally require a router restart; request parameters can often vary per request.

## Version and sources

- **llama.cpp release:** `v0.4.1`
- **Referenced nightly build:** `b10964` (the nightly build named by the `v0.4.1` release)
- **Reference date:** 2026-09-21
- **Primary reference:** [llama.cpp `v0.4.1` server README](https://github.com/ggml-org/llama.cpp/blob/v0.4.1/tools/server/README.md)
- **Release notes:** [llama.cpp `v0.4.1`](https://github.com/ggml-org/llama.cpp/releases/tag/v0.4.1)
- **Application sources inspected:** `backend/src/llamawebui/domain/model_profile.py`, `backend/src/llamawebui/domain/router_lifecycle.py`, `backend/src/llamawebui/app.py`, and runtime capability probing.

The upstream README is generated from the executable's help definitions. A particular installed binary can differ, so LlamaWebUI probes `llama-server --help` and rejects a profile option that the selected executable does not advertise.

## Support summary

| Area | First-class support |
| --- | ---: |
| Model path and per-model preset settings | X |
| GPU layers, device placement, split mode, tensor split | 1 option: GPU layers only |
| Context, KV cache, Flash Attention, loading and fitting | X for the typed fields listed below |
| CPU threads and batching | X for `--threads`, `--batch-size`, and `--ubatch-size` |
| Router host, port, model preset, API-key file | X |
| Sampling and speculative decoding startup defaults | No first-class controls |
| Multimodal, LoRA, embeddings, reranking, tools, MCP, TLS | No first-class controls |

## Effect tag glossary

The **Main effect tags** column is intended for compact UI chips. Tags are lowercase, stable identifiers rather than prose. Multiple tags may apply to one option.

| Tag | Definition |
| --- | --- |
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

## Complete option reference

### Common and model/runtime options

| Argument and aliases | Upstream definition | Main effect tags | LlamaWebUI support |
| --- | --- | --- | :---: |
| `-h`, `--help`, `--usage` | Print usage and exit. | `diagnostics` |  |
| `--version` | Show version and build information. | `diagnostics` |  |
| `-cl`, `--cache-list` | Show models in the llama.cpp cache. | `discovery` |  |
| `--completion-bash` | Print a sourceable Bash completion script. | `usability` |  |
| `-t`, `--threads N` | CPU threads used during generation. | `decodespeed`, `cpu` | X |
| `-tb`, `--threads-batch N` | CPU threads used for batch/prompt processing. | `prefillspeed`, `cpu` |  |
| `-C`, `--cpu-mask M` | CPU affinity mask. | `cpu` |  |
| `-Cr`, `--cpu-range lo-hi` | CPU affinity range. | `cpu` |  |
| `--cpu-strict 0\|1` | Use strict CPU placement. | `cpu`, `latency` |  |
| `--prio N` | Process/thread priority. | `latency`, `cpuusage` |  |
| `--poll 0...100` | Polling level while waiting for work. | `latency`, `cpuusage` |  |
| `-Cb`, `--cpu-mask-batch M` | CPU affinity mask for batch work. | `cpu`, `prefillspeed` |  |
| `-Crb`, `--cpu-range-batch lo-hi` | CPU range for batch work. | `cpu`, `prefillspeed` |  |
| `--cpu-strict-batch 0\|1` | Strict CPU placement for batch work. | `cpu`, `prefillspeed` |  |
| `--prio-batch N` | Priority for batch work. | `prefillspeed`, `cpuusage` |  |
| `--poll-batch 0\|1` | Polling for batch work. | `prefillspeed`, `cpuusage` |  |
| `-c`, `--ctx-size N` | Prompt context size; `0` loads the model value. | `vram`, `ram`, `prefillspeed`, `concurrency` | X |
| `-n`, `--predict N`, `--n-predict N` | Maximum generated tokens; `-1` means unlimited. | `decodespeed`, `usability` |  |
| `-b`, `--batch-size N` | Logical maximum batch size. | `prefillspeed`, `vram`, `throughput` | X |
| `-ub`, `--ubatch-size N` | Physical maximum batch size. | `prefillspeed`, `vram`, `throughput` | X |
| `--keep N` | Tokens retained when context is shifted; `-1` means all. | `context`, `prefillspeed`, `quality` |  |
| `--swa-full` | Use a full-size Sliding Window Attention cache. | `vramusage`, `ramusage`, `context` |  |
| `-fa`, `--flash-attn on\|off\|auto` | Enable, disable, or automatically select Flash Attention. | `prefillspeed`, `vram`, `backend` | X |
| `--perf`, `--no-perf` | Enable internal libllama performance timings. | `diagnostics` |  |
| `-e`, `--escape`, `--no-escape` | Process escape sequences in input. | `usability` |  |
| `--rope-scaling none\|linear\|yarn` | Select RoPE frequency scaling. | `context`, `quality`, `prefillspeed` |  |
| `--rope-scale N` | RoPE context scaling multiplier. | `context`, `vramusage`, `quality` |  |
| `--rope-freq-base N` | Override RoPE base frequency. | `context`, `quality` |  |
| `--rope-freq-scale N` | RoPE frequency scale; expands context by `1/N`. | `context`, `quality` |  |
| `--yarn-orig-ctx N` | Original model context for YaRN. | `context`, `quality` |  |
| `--yarn-ext-factor N` | YaRN extrapolation/interpolation mix. | `context`, `quality` |  |
| `--yarn-attn-factor N` | YaRN attention magnitude factor. | `context`, `quality` |  |
| `--yarn-beta-slow N` | YaRN high correction dimension/alpha. | `context`, `quality` |  |
| `--yarn-beta-fast N` | YaRN low correction dimension/beta. | `context`, `quality` |  |
| `-kvo`, `--kv-offload`, `-nkvo`, `--no-kv-offload` | Enable or disable KV-cache device offload. | `vramusage`, `ramusage`, `decodespeed` |  |
| `--repack`, `-nr`, `--no-repack` | Enable or disable weight repacking. | `vramusage`, `decodespeed` |  |
| `--no-host` | Bypass host buffers to permit extra buffers. | `vramusage`, `ramusage`, `capacity` |  |
| `-ctk`, `--cache-type-k TYPE` | KV-cache data type for K. | `vram`, `ram`, `prefillspeed`, `decodespeed`, `quality` | X |
| `-ctv`, `--cache-type-v TYPE` | KV-cache data type for V. | `vram`, `ram`, `prefillspeed`, `decodespeed`, `quality` | X |
| `-dt`, `--defrag-thold N` | KV-cache defragmentation threshold (deprecated). | `vramusage`, `decodespeed`, `deprecated` |  |
| `--rpc SERVERS` | Comma-separated RPC offload servers. | `network`, `vramusage`, `decodespeed` |  |
| `-lm`, `--load-mode MODE` | Model loading mode: `auto`, `none`, `mmap`, `mlock`, `mmap+mlock`, or `dio`. | `loadspeed`, `ram`, `usability` | X |
| `-lzm`, `--lazy-mode MODE` | On-demand reading of selected large tensors: `on`, `auto`, or `off`. | `ram`, `vram`, `loadspeed`, `decodespeed` | X |
| `--numa TYPE` | NUMA placement strategy such as `distribute`, `isolate`, or `numactl`. | `cpu`, `prefillspeed`, `decodespeed` |  |
| `-dev`, `--device DEVICES` | Devices used for offload; `none` disables offload. | `vramusage`, `ramusage`, `decodespeed` |  |
| `--list-devices` | List available devices and exit. | `discovery`, `diagnostics` |  |
| `-ot`, `--override-tensor PATTERN=TYPE,...` | Override tensor buffer type by tensor-name pattern. | `vram`, `ram`, `decodespeed`, `advanced` | X |
| `-cmoe`, `--cpu-moe` | Keep all MoE weights on CPU. | `vramusage`, `ramusage`, `decodespeed` |  |
| `-ncmoe`, `--n-cpu-moe N` | Keep first `N` layers of MoE weights on CPU. | `vramusage`, `ramusage`, `decodespeed` |  |
| `-ncffn`, `--n-cpu-ffn N` | Keep first `N` dense FFN layers on CPU. | `vramusage`, `ramusage`, `decodespeed` |  |
| `-ngl`, `--gpu-layers`, `--n-gpu-layers N` | Maximum model layers stored in VRAM; also accepts `auto` or `all`. | `vram`, `decodespeed`, `cpu` | X |
| `-sm`, `--split-mode none\|layer\|row\|tensor` | Split model across multiple GPUs. | `vramusage`, `throughput`, `latency` |  |
| `-ts`, `--tensor-split N0,N1,...` | Proportional model split across GPUs. | `vramusage`, `multigpu` |  |
| `-mg`, `--main-gpu INDEX` | Main GPU for single-device or row-split operation. | `vramusage`, `multigpu` |  |
| `-fit`, `--fit on\|off` | Adjust unset settings to fit device memory. | `vram`, `usability`, `reliability` | X |
| `-fitt`, `--fit-target MiB0,...` | Per-device memory margin for `--fit`. | `vramusage`, `reliability` |  |
| `-fitc`, `--fit-ctx N` | Minimum context that `--fit` may choose. | `context`, `vramusage`, `reliability` |  |
| `--check-tensors` | Validate model tensor data for invalid values. | `reliability`, `loadspeed` |  |
| `--override-kv KEY=TYPE:VALUE,...` | Override model metadata values. | `quality`, `context`, `advanced` |  |
| `--op-offload`, `--no-op-offload` | Offload host tensor operations to a device. | `vramusage`, `decodespeed` |  |
| `--lora FNAME` | Load one or more LoRA adapters. | `vramusage`, `quality`, `concurrency` |  |
| `--lora-scaled FNAME:SCALE,...` | Load LoRA adapters with explicit scales. | `vramusage`, `quality` |  |
| `--control-vector FNAME` | Load control vectors. | `quality`, `vramusage` |  |
| `--control-vector-scaled FNAME:SCALE,...` | Load control vectors with scales. | `quality` |  |
| `--control-vector-layer-range START END` | Layer range for control vectors. | `quality`, `decodespeed` |  |
| `-m`, `--model FNAME` | Path to the model. | `model`, `vram`, `discovery` | X |
| `-mu`, `--model-url URL` | Download a model from a URL. | `network`, `diskusage`, `discovery` |  |
| `-dr`, `--docker-repo REPO` | Select a Docker Hub model. | `network`, `discovery`, `usability` |  |
| `-hf`, `-hfr`, `--hf-repo REPO[:QUANT]` | Select a Hugging Face model/quantization; may auto-download mmproj. | `network`, `diskusage`, `discovery` |  |
| `-hff`, `--hf-file FILE` | Select a specific Hugging Face file. | `network`, `discovery` |  |
| `-hft`, `--hf-token TOKEN` | Hugging Face access token or `HF_TOKEN`. | `security`, `network` |  |
| `--log-disable` | Disable logging. | `diagnostics` |  |
| `--log-file FNAME` | Write logs to a file. | `diagnostics`, `diskusage` |  |
| `--log-jsonl`, `--no-log-jsonl` | Emit structured JSONL logs. | `diagnostics`, `usability` |  |
| `--log-colors on\|off\|auto` | Control colored logs. | `usability` |  |
| `-v`, `--verbose`, `--log-verbose` | Enable maximum verbosity. | `diagnostics`, `diskusage` |  |
| `--offline` | Use cache only and prevent network access. | `network`, `reliability`, `security` |  |
| `-lv`, `--verbosity`, `--log-verbosity N` | Set log verbosity threshold. | `diagnostics`, `diskusage` |  |
| `--log-prefix`, `--no-log-prefix` | Enable/disable log prefixes. | `diagnostics`, `usability` |  |
| `--log-timestamps`, `--no-log-timestamps` | Enable/disable log timestamps. | `diagnostics`, `usability` |  |
| `--spec-draft-type-k TYPE` / `-ctkd`, `--cache-type-k-draft TYPE` | Draft-model K-cache type. | `vramusage`, `ramusage`, `decodespeed` |  |
| `--spec-draft-type-v TYPE` / `-ctvd`, `--cache-type-v-draft TYPE` | Draft-model V-cache type. | `vramusage`, `ramusage`, `decodespeed` |  |

### Sampling options

These options affect token selection and output behavior. They normally have little direct effect on model-weight VRAM; some can add small temporary/logit-processing work. LlamaWebUI does not currently expose them as startup profile fields.

| Argument and aliases | Upstream definition | Main effect tags | LlamaWebUI support |
| --- | --- | --- | :---: |
| `--samplers SAMPLERS` | Sampler chain and order, separated by `;`. | `quality`, `decodespeed` |  |
| `-s`, `--seed SEED` | RNG seed; `-1` selects a random seed. | `quality`, `usability` |  |
| `--sampler-seq`, `--sampling-seq SEQUENCE` | Simplified sampler sequence. | `quality` |  |
| `--ignore-eos` | Ignore EOS and continue generation. | `quality`, `decodespeed`, `usability` |  |
| `--temp`, `--temperature N` | Temperature. | `quality` |  |
| `--top-k N` | Restrict sampling to the top K tokens. | `quality`, `decodespeed` |  |
| `--top-p N` | Nucleus sampling probability threshold. | `quality`, `decodespeed` |  |
| `--min-p N` | Relative minimum token probability. | `quality`, `decodespeed` |  |
| `--top-nsigma`, `--top-n-sigma N` | Top-n-sigma sampling. | `quality`, `decodespeed` |  |
| `--xtc-probability N` | Probability of XTC token removal. | `quality`, `decodespeed` |  |
| `--xtc-threshold N` | XTC probability threshold. | `quality` |  |
| `--typical`, `--typical-p N` | Locally typical sampling. | `quality`, `decodespeed` |  |
| `--repeat-last-n N` | Token window considered for repetition penalty. | `quality`, `decodespeed`, `ramusage` |  |
| `--repeat-penalty N` | Repetition penalty. | `quality` |  |
| `--presence-penalty N` | Presence penalty. | `quality` |  |
| `--frequency-penalty N` | Frequency penalty. | `quality` |  |
| `--dry-multiplier N` | DRY repetition penalty multiplier. | `quality`, `decodespeed` |  |
| `--dry-base N` | DRY exponential base. | `quality` |  |
| `--dry-allowed-length N` | DRY unpenalized repetition length. | `quality` |  |
| `--dry-penalty-last-n N` | DRY scan window. | `quality`, `decodespeed` |  |
| `--dry-sequence-breaker STRING` | DRY sequence breaker. | `quality` |  |
| `--adaptive-target N` | Adaptive-p target probability. | `quality`, `decodespeed` |  |
| `--adaptive-decay N` | Adaptive-p decay rate. | `quality` |  |
| `--dynatemp-range N` | Dynamic temperature range. | `quality`, `decodespeed` |  |
| `--dynatemp-exp N` | Dynamic temperature exponent. | `quality` |  |
| `--mirostat N` | Enable Mirostat 1 or 2. | `quality`, `decodespeed` |  |
| `--mirostat-lr N` | Mirostat learning rate. | `quality` |  |
| `--mirostat-ent N` | Mirostat target entropy. | `quality` |  |
| `-l`, `--logit-bias TOKEN_ID(+/-)BIAS` | Bias token likelihoods. | `quality`, `decodespeed` |  |
| `--grammar GRAMMAR` | Inline grammar constraint. | `quality`, `decodespeed` |  |
| `--grammar-file FNAME` | Grammar from a file. | `quality`, `decodespeed` |  |
| `-j`, `--json-schema SCHEMA` | JSON-schema output constraint. | `quality`, `decodespeed` |  |
| `-jf`, `--json-schema-file FILE` | JSON schema from a file. | `quality`, `decodespeed` |  |
| `-bs`, `--backend-sampling` | Experimental backend sampling. | `backend`, `decodespeed` |  |

### Server, concurrency, HTTP, router, and security options

| Argument and aliases | Upstream definition | Main effect tags | LlamaWebUI support |
| --- | --- | --- | :---: |
| `-lcs`, `--lookup-cache-static FNAME` | Static lookup cache for lookup decoding. | `decodespeed`, `diskusage`, `ramusage` |  |
| `-lcd`, `--lookup-cache-dynamic FNAME` | Dynamic lookup cache updated during generation. | `decodespeed`, `diskusage`, `ramusage` |  |
| `--kv-unified-per-slot N` | Context limit per parallel slot. | `vramusage`, `concurrency`, `context` |  |
| `-ctxcp`, `--ctx-checkpoints`, `--swa-checkpoints N` | Maximum context checkpoints per slot. | `ramusage`, `context`, `prefillspeed` |  |
| `-cms`, `--checkpoint-min-step N` | Minimum token spacing between checkpoints. | `ramusage`, `context`, `prefillspeed` |  |
| `-cram`, `--cache-ram N` | Maximum prompt-cache size in MiB; `0` disables, `-1` removes the limit. | `ramusage`, `prefillspeed`, `diskusage` | X |
| `-kvu`, `--kv-unified`, `-no-kvu`, `--no-kv-unified` | Use one unified KV buffer across sequences. | `vramusage`, `concurrency`, `ramusage` |  |
| `--cache-idle-slots`, `--no-cache-idle-slots` | Save/clear idle slots in the prompt cache. | `prefillspeed`, `ramusage` |  |
| `--context-shift`, `--no-context-shift` | Enable context shifting for infinite generation. | `context`, `usability`, `prefillspeed` |  |
| `-r`, `--reverse-prompt PROMPT` | Stop at a reverse prompt in interactive mode. | `usability`, `quality` |  |
| `-sp`, `--special` | Enable special-token output. | `usability`, `quality` |  |
| `--warmup`, `--no-warmup` | Perform an empty warmup run. | `loadspeed`, `latency`, `usability` |  |
| `--spm-infill` | Use Suffix/Prefix/Middle infill ordering. | `quality`, `usability` |  |
| `--pooling none\|mean\|cls\|last\|rank` | Embedding pooling mode. | `quality`, `ramusage`, `usability` |  |
| `-np`, `--parallel N` | Number of server slots; `-1` means automatic. | `concurrency`, `vramusage`, `throughput` |  |
| `-cb`, `--cont-batching`, `-nocb`, `--no-cont-batching` | Enable/disable continuous batching. | `concurrency`, `throughput`, `latency` |  |
| `-mm`, `--mmproj FILE` | Multimodal projector path. | `multimodal`, `vramusage`, `ramusage`, `loadspeed` |  |
| `-mmu`, `--mmproj-url URL` | Download a multimodal projector from a URL. | `multimodal`, `network`, `diskusage` |  |
| `--mmproj-auto`, `--no-mmproj`, `--no-mmproj-auto` | Automatically use an available projector. | `multimodal`, `discovery`, `vramusage` |  |
| `--mmproj-offload`, `--no-mmproj-offload` | Offload projector to device. | `multimodal`, `vramusage`, `decodespeed` |  |
| `-mmdev`, `--mmproj-device DEVICE` | Device for projector offload. | `multimodal`, `vramusage`, `decodespeed` |  |
| `--image-min-tokens N` | Minimum tokens allocated per image. | `multimodal`, `context`, `prefillspeed` |  |
| `--image-max-tokens N` | Maximum tokens allocated per image. | `multimodal`, `context`, `prefillspeed` |  |
| `--mtmd-batch-max-tokens N` | Maximum image tokens per batch. | `multimodal`, `prefillspeed`, `vramusage` |  |
| `--video-fps N` | Target video frame rate. | `multimodal`, `prefillspeed`, `usability` |  |
| `--video-timestamp-interval N` | Video timestamp interval in milliseconds. | `multimodal`, `prefillspeed` |  |
| `--video-ffmpeg-dir DIR` | Directory containing `ffmpeg` and `ffprobe`. | `multimodal`, `usability` |  |
| `-a`, `--alias STRING` | Comma-separated API model aliases. | `usability`, `routing` |  |
| `--tags STRING` | Informational comma-separated model tags. | `usability`, `metadata` |  |
| `--embd-normalize N` | Embedding normalization mode. | `quality`, `embeddings` |  |
| `--host HOST` | Listen address or Unix socket path. | `network`, `security`, `usability` | X |
| `--port PORT` | Listen port; default `8080`. | `network`, `usability` | X |
| `--reuse-port` | Allow multiple sockets on one port. | `network`, `concurrency`, `security` |  |
| `--path PATH` | Directory for static files. | `usability`, `diskusage` |  |
| `--cors-origins ORIGINS` | Allowed CORS origins. | `security`, `network` |  |
| `--cors-methods METHODS` | Allowed CORS methods. | `security`, `network` |  |
| `--cors-headers HEADERS` | Allowed CORS headers. | `security`, `network` |  |
| `--cors-credentials`, `--no-cors-credentials` | Allow credentials in CORS. | `security`, `network` |  |
| `--api-prefix PREFIX` | URL path prefix. | `network`, `usability` |  |
| `--ui-config`, `--webui-config JSON` | Default embedded Web UI settings. | `usability` |  |
| `--ui-config-file`, `--webui-config-file PATH` | Default Web UI settings file. | `usability`, `diskusage` |  |
| `--ui-mcp-proxy`, `--webui-mcp-proxy`, `--no-ui-mcp-proxy`, `--no-webui-mcp-proxy` | Experimental browser MCP CORS proxy. | `security`, `network`, `usability` |  |
| `--tools TOOL1,TOOL2,...` | Enable built-in server tools. | `security`, `usability`, `network` |  |
| `--tools-runtime OPTION` | Run tools in Docker, Podman, SSH, or host runtime. | `security`, `network`, `usability` |  |
| `--mcp-servers-config PATH` | MCP server definitions from a JSON file. | `security`, `usability`, `network` |  |
| `--mcp-servers-json JSON` | Inline MCP server definitions. | `security`, `usability`, `network` |  |
| `-ag`, `--agent`, `-no-ag`, `--no-agent` | Enable CORS proxy and all built-in tools. | `security`, `network`, `usability` |  |
| `--ui`, `--webui`, `--no-ui`, `--no-webui` | Enable/disable native Web UI. | `usability` |  |
| `--embedding`, `--embeddings` | Restrict server to embedding use. | `embeddings`, `usability` |  |
| `--rerank`, `--reranking` | Enable reranking endpoint. | `embeddings`, `usability` |  |
| `--api-key KEY` | Inline API key(s). | `security`, `usability` |  |
| `--api-key-file FNAME` | API keys, one per line. | `security`, `usability` | X |
| `--ssl-key-file FNAME` | PEM private key. | `security`, `network` |  |
| `--ssl-cert-file FNAME` | PEM certificate. | `security`, `network` |  |
| `--chat-template-kwargs STRING` | JSON values supplied to the chat template. | `quality`, `usability` |  |
| `-to`, `--timeout N` | HTTP read/write timeout in seconds. | `network`, `reliability` |  |
| `--sse-ping-interval N` | SSE idle ping interval; `-1` disables. | `network`, `diagnostics` |  |
| `--threads-http N` | HTTP request-processing threads. | `concurrency`, `cpuusage`, `latency` |  |
| `--cache-prompt`, `--no-cache-prompt` | Enable/disable prompt KV caching. | `prefillspeed`, `ramusage`, `vramusage` |  |
| `--cache-reuse N` | Minimum chunk size for KV-shift cache reuse. | `prefillspeed`, `ramusage` |  |
| `--metrics` | Enable Prometheus metrics endpoint. | `diagnostics`, `usability` |  |
| `--props` | Allow changing global properties through `/props`. | `usability`, `security` |  |
| `--slots`, `--no-slots` | Expose/disable slot monitoring endpoint. | `diagnostics`, `usability` |  |
| `--slot-save-path PATH` | Directory for saved slot KV caches. | `diskusage`, `ramusage`, `prefillspeed` |  |
| `--media-path PATH` | Directory allowed for local media files. | `multimodal`, `security`, `diskusage` |  |
| `--models-dir PATH` | Router model directory. | `discovery`, `diskusage`, `usability` |  |
| `--models-preset PATH` | Router model preset INI file. | `discovery`, `usability`, `model` | X |
| `--models-max N` | Maximum simultaneously loaded router models; `0` unlimited. | `concurrency`, `vramusage`, `ramusage` |  |
| `--models-autoload`, `--no-models-autoload` | Automatically load models on request. | `loadspeed`, `usability`, `vramusage` |  |
| `--jinja`, `--no-jinja` | Enable/disable Jinja chat templates. | `quality`, `usability`, `toolcalling` |  |
| `--reasoning-format FORMAT` | Parse/return reasoning as none, DeepSeek, or legacy. | `quality`, `usability` |  |
| `-rea`, `--reasoning on\|off\|auto` | Enable/disable/detect reasoning. | `quality`, `decodespeed`, `usability` |  |
| `--reasoning-effort LEVEL` | Pass reasoning effort such as `minimal`, `low`, `medium`, `high`, or `max`. | `quality`, `decodespeed` |  |
| `--reasoning-budget N` | Thinking token budget; `-1` unlimited, `0` immediate end. | `quality`, `decodespeed`, `usability` |  |
| `--reasoning-budget-message MESSAGE` | Message inserted when thinking budget is exhausted. | `quality`, `usability` |  |
| `--reasoning-preserve`, `--no-reasoning-preserve` | Preserve reasoning in full history. | `quality`, `prefillspeed`, `context` | X |
| `--chat-template JINJA_TEMPLATE` | Select a built-in/custom Jinja chat template. | `quality`, `usability`, `toolcalling` |  |
| `--chat-template-file FILE` | Load a Jinja template from a file. | `quality`, `usability`, `toolcalling` |  |
| `--skip-chat-parsing`, `--no-skip-chat-parsing` | Force pure content parsing. | `quality`, `toolcalling`, `usability` |  |
| `--prefill-assistant`, `--no-prefill-assistant` | Prefill an assistant message when the last message is assistant-authored. | `prefillspeed`, `quality` |  |
| `-sps`, `--slot-prompt-similarity SIMILARITY` | Minimum prompt similarity for slot reuse. | `prefillspeed`, `concurrency`, `ramusage` |  |
| `--lora-init-without-apply` | Load LoRAs at scale zero until changed through the API. | `vramusage`, `quality`, `usability` |  |
| `--sleep-idle-seconds SECONDS` | Sleep after inactivity; `-1` disables. | `vramusage`, `ramusage`, `loadspeed` |  |
| `--log-prompts-dir PATH` | Write prompts to a directory for debugging. | `diagnostics`, `diskusage`, `security` |  |

### Speculative decoding options

| Argument and aliases | Upstream definition | Main effect tags | LlamaWebUI support |
| --- | --- | --- | :---: |
| `--spec-draft-hf`, `-hfd`, `-hfrd`, `--hf-repo-draft REPO[:QUANT]` | Draft model Hugging Face repository. | `network`, `diskusage`, `discovery`, `decodespeed` |  |
| `--spec-draft-threads`, `-td`, `--threads-draft N` | Draft-model generation threads. | `decodespeed`, `cpuusage` |  |
| `--spec-draft-threads-batch`, `-tbd`, `--threads-batch-draft N` | Draft-model batch threads. | `prefillspeed`, `cpuusage` |  |
| `--spec-draft-cpu-mask`, `-Cd`, `--cpu-mask-draft M` | Draft-model CPU affinity mask. | `cpu`, `decodespeed` |  |
| `--spec-draft-cpu-range`, `-Crd`, `--cpu-range-draft lo-hi` | Draft-model CPU range. | `cpu`, `decodespeed` |  |
| `--spec-draft-cpu-strict`, `--cpu-strict-draft 0\|1` | Strict draft CPU placement. | `cpu`, `latency` |  |
| `--spec-draft-prio`, `--prio-draft N` | Draft thread priority. | `latency`, `cpuusage` |  |
| `--spec-draft-poll`, `--poll-draft 0\|1` | Draft polling. | `latency`, `cpuusage` |  |
| `--spec-draft-cpu-mask-batch`, `-Cbd`, `--cpu-mask-batch-draft M` | Draft batch CPU affinity. | `cpu`, `prefillspeed` |  |
| `--spec-draft-cpu-strict-batch`, `--cpu-strict-batch-draft 0\|1` | Strict draft batch placement. | `cpu`, `prefillspeed` |  |
| `--spec-draft-prio-batch`, `--prio-batch-draft N` | Draft batch priority. | `prefillspeed`, `cpuusage` |  |
| `--spec-draft-poll-batch`, `--poll-batch-draft 0\|1` | Draft batch polling. | `prefillspeed`, `cpuusage` |  |
| `--spec-draft-override-tensor`, `-otd`, `--override-tensor-draft ...` | Draft tensor buffer overrides. | `vramusage`, `ramusage`, `decodespeed` |  |
| `--spec-draft-cpu-moe`, `-cmoed`, `--cpu-moe-draft` | Keep all draft MoE weights on CPU. | `vramusage`, `ramusage`, `decodespeed` |  |
| `--spec-draft-n-cpu-moe`, `--spec-draft-ncmoe`, `-ncmoed`, `--n-cpu-moe-draft N` | Keep first N draft MoE layers on CPU. | `vramusage`, `ramusage`, `decodespeed` |  |
| `--spec-draft-n-max N` | Maximum draft tokens. | `decodespeed`, `throughput` |  |
| `--spec-draft-n-min N` | Minimum draft tokens. | `decodespeed`, `latency` |  |
| `--spec-synth-len L` | Synthetic acceptance length for benchmarking. | `diagnostics`, `benchmarking` |  |
| `--spec-synth-rates P0,P1,...` | Synthetic acceptance probabilities for benchmarking. | `diagnostics`, `benchmarking` |  |
| `--spec-draft-p-split`, `--draft-p-split P` | Speculative split probability. | `decodespeed`, `throughput` |  |
| `--spec-draft-p-min`, `--draft-p-min P` | Minimum speculative probability. | `decodespeed`, `latency` |  |
| `--spec-draft-backend-sampling`, `--no-spec-draft-backend-sampling` | Draft backend sampling. | `backend`, `decodespeed`, `vramusage` |  |
| `--spec-draft-device`, `-devd`, `--device-draft DEVICES` | Draft offload devices. | `vramusage`, `decodespeed` |  |
| `--spec-draft-ngl`, `-ngld`, `--gpu-layers-draft`, `--n-gpu-layers-draft N` | Draft layers in VRAM. | `vramusage`, `decodespeed` |  |
| `--spec-draft-model`, `-md`, `--model-draft FNAME` | Draft model path. | `model`, `vramusage`, `decodespeed` |  |
| `--spec-type none,draft-simple,draft-eagle3,draft-mtp,draft-dflash,draft-dspark,ngram-simple,ngram-map-k,ngram-map-k4v,ngram-mod,ngram-cache` | Speculative decoding method list. | `decodespeed`, `throughput`, `vramusage` |  |
| `--spec-ngram-mod-n-min N` | Minimum n-gram tokens for ngram-mod. | `decodespeed`, `throughput` |  |
| `--spec-ngram-mod-n-max N` | Maximum n-gram tokens for ngram-mod. | `decodespeed`, `throughput` |  |
| `--spec-ngram-mod-n-match N` | ngram-mod lookup length. | `decodespeed`, `throughput` |  |
| `--spec-ngram-simple-size-n N` | Lookup n-gram length. | `decodespeed`, `throughput` |  |
| `--spec-ngram-simple-size-m N` | Draft m-gram length. | `decodespeed`, `throughput` |  |
| `--spec-ngram-simple-min-hits N` | Minimum simple n-gram hits. | `decodespeed`, `throughput` |  |
| `--spec-ngram-map-k-size-n N` | map-k lookup n-gram length. | `decodespeed`, `throughput` |  |
| `--spec-ngram-map-k-size-m N` | map-k draft m-gram length. | `decodespeed`, `throughput` |  |
| `--spec-ngram-map-k-min-hits N` | Minimum map-k hits. | `decodespeed`, `throughput` |  |
| `--spec-ngram-map-k4v-size-n N` | map-k4v lookup n-gram length. | `decodespeed`, `throughput` |  |
| `--spec-ngram-map-k4v-size-m N` | map-k4v draft m-gram length. | `decodespeed`, `throughput` |  |
| `--spec-ngram-map-k4v-min-hits N` | Minimum map-k4v hits. | `decodespeed`, `throughput` |  |
| `--draft`, `--draft-n`, `--draft-max N` | Removed; use `--spec-draft-n-max` or `--spec-ngram-mod-n-max`. | `deprecated`, `compatibility` |  |
| `--draft-min`, `--draft-n-min N` | Removed; use the corresponding `spec-*` option. | `deprecated`, `compatibility` |  |
| `--spec-ngram-size-n N` | Removed; use the respective n-gram option. | `deprecated`, `compatibility` |  |
| `--spec-ngram-size-m N` | Removed; use the respective n-gram option. | `deprecated`, `compatibility` |  |
| `--spec-ngram-min-hits N` | Removed; use the respective n-gram option. | `deprecated`, `compatibility` |  |
| `--spec-default` | Enable the default speculative decoding configuration. | `decodespeed`, `throughput`, `vramusage` |  |

## How support is implemented in this app

The current first-class profile fields serialize to model-preset keys in `backend/src/llamawebui/domain/model_profile.py`: `model`, `no-reasoning-preserve`, `n-gpu-layers`, `ctx-size`, `flash-attn`, `load-mode`, `lazy-mode`, `cache-ram`, `fit`, `override-tensor`, `cache-type-k`, `cache-type-v`, `threads`, `batch-size`, and `ubatch-size`.

The fixed router launch in `backend/src/llamawebui/domain/router_lifecycle.py` emits `--models-preset`, `--host`, `--port`, and, when configured, `--api-key-file`. Runtime probing parses the selected executable's `--help` output, so the same profile is validated against the actual installed build rather than this reference version.

The app's Advanced options preserve a structured option name/value pair and validate that the selected runtime advertises the option. They are suitable for experimentation, but they are not yet typed, explained, or tuned by the UI. Future versioned documents should be generated or reviewed again whenever the llama.cpp release changes.
