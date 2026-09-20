# LlamaWebUI Implementation Progress

**Updated:** 2026-09-20
**Branch:** `alpha`
**Baseline commit:** `69c4fd5` (`feat: add model discovery and download workflows`)
**Active phase:** Frontend implementation

This is the session handoff document. Update it after each completed implementation slice. The authoritative requirements remain in [llama-web-ui-plan.md](llama-web-ui-plan.md).

## Current Position

The delivery plan has eight numbered phases (`0` through `7`). Work has intentionally not been strictly sequential: the durable Hugging Face download core from Phase 5 was implemented before router supervision in Phase 4.

| Phase | Status | Implemented | Important remaining work |
| --- | --- | --- | --- |
| 0. Feasibility spikes | Partial | Real llama.cpp b11053 CPU and b11060 CUDA probed; required Qwen flags confirmed; generated preset accepted; authenticated non-streaming, visible streaming, parseable tool-call, and Windows parent/child process-tree shutdown verified | API-key reload behavior, older-build comparison |
| 1. Application foundation | Advanced partial | Python package, FastAPI, settings, SQLite, Alembic, portable data directory, unified bounded event broker with sequencing/replay/reconciliation, React/Vite operator shell | Publish remaining download/runtime/profile state changes, structured/redacted logging, single-instance lock, diagnostics, static frontend packaging |
| 2. Runtime manager | Advanced partial | Register/list/get/reprobe/remove; option/device capability parsing; in-use deletion guard; GitHub release asset discovery, stable-to-build resolution, digest-checked staged archive extraction, archive path hardening, capability probing, and atomic promotion | UI asset/backend selection, CUDA companion asset grouping, switching/rollback |
| 3. Local library and profiles | In progress | Profile persistence/deletion, typed Qwen options, capability validation, shard completeness, deterministic atomic single/combined preset writing, validated completed-download projection, broken-profile health/provenance, profile prefill and exact re-download repair | General directory scanning, GGUF metadata, durable logical model records, command import/export |
| 4. Server lifecycle | In progress | Router state machine, validated argument vector, process-group launch, single-process supervisor, HTTP readiness polling, bounded log tail, crash observation, owned process-tree graceful/forced shutdown, serialized status/start/stop/restart API, durable run and restart-attempt history, safe port preflight, bounded crash recovery with rapid-failure suppression, native model list/load/unload/SSE APIs, lifecycle/model event publication through unified replayable `/api/events` | Real-runtime SSE acceptance |
| 5. Hugging Face and downloads | Advanced partial | Search, repository manifests, quant/shard grouping, revision-pinned durable jobs, in-file progress, interruptible child-process transfers, resumable pause, prompt cancel cleanup, managed artifact deletion, exact pinned re-download, staging, size verification, atomic publication, startup reconciliation, terminal-job clearing, responsive Discover and Downloads workflows | Retry/backoff, periodic disk checks, checksum/ETag verification, event streaming, projector association, general library reconciliation |
| 6. Tokens and onboarding | Advanced partial | Show-once token creation, HMAC metadata, last-four/name/expiry-note listing, permanent revocation, atomic restricted native key file, authenticated router launch/control calls, live-model OpenCode generation, Access frontend workflow | End-to-end authenticated connection tests remain blocked on an available model/profile |
| 7. Hardening and release | Not started | Unit quality gate established | Packaging, CI/platform matrix, accessibility, backup/restore, offline behavior, operator docs |

No phase has met its complete release gate yet because each gate includes later integration, frontend, packaging, or real-runtime acceptance work.

## Verified Baseline

From `backend/` using `..\.venv\Scripts\python.exe`:

```powershell
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m mypy src/llamawebui
```

Current validation:

- 151 tests passed.
- 91.36% total coverage; configured floor is 90% with branch coverage enabled.
- Ruff passed.
- Strict mypy passed for 39 source files.
- Two dependency deprecation warnings remain: Starlette/httpx and AnyIO `BlockingPortal`.

Frontend foundation validation:

- Vite production build passed; JavaScript bundle is 315.66 kB.
- Vitest/Testing Library passed: 18 component integration tests.
- Desktop 1440x1000 and mobile 390x844 browser checks passed without horizontal overflow.
- Live FastAPI queries and responsive navigation were verified in the browser.
- Runtime registration was accepted end to end against local llama.cpp b11053 and displayed build `0.4.1-dev` as ready.

## Implemented Backend Surfaces

- Configuration and startup: `backend/src/llamawebui/config.py`, `app.py`, `__main__.py`
- Persistence: `database.py`, `models.py`, Alembic migrations `0001` through `0006`
- Runtime capability and registry: `domain/runtime_capabilities.py`, `services/runtime_probe.py`, `services/runtime_registry.py`
- Profiles/presets: `domain/model_profile.py`, `services/profile_registry.py`
- Hugging Face discovery: `domain/model_manifest.py`, `services/huggingface_catalog.py`
- Downloads: `domain/download_job.py`, `services/download_registry.py`, `services/download_worker.py`, `services/download_coordinator.py`
- Router lifecycle: `domain/router_lifecycle.py`, `services/router_supervisor.py`, `services/server_run_registry.py`, `services/router_client.py`
- Native router model control: guarded `GET /api/server/models`, `POST /api/server/models/load`, and `POST /api/server/models/unload`; exact llama.cpp payloads, optional list reload, preserved metadata, and sanitized upstream errors
- Native router model events: typed `/models/sse` parsing with frame/media validation, guarded `GET /api/server/models/events` relay, sanitized terminal errors, and disconnect-driven upstream cleanup
- Unified events: `GET /api/events` with monotonic IDs, bounded history, atomic replay/live handoff, `Last-Event-ID` or query cursors, stale/future cursor rejection, slow-consumer reconciliation, router lifecycle publication after durable persistence, and lifecycle-owned native event synchronization/reconnect
- Crash recovery: configurable maximum attempts/window/backoff, readiness checks on each relaunch, rapid-failure suppression, cancellation on explicit stop/restart/shutdown, and a durable run row per attempt
- Process-tree shutdown: `CTRL_BREAK_EVENT` to the owned Windows process group, bounded wait, then `taskkill /PID ... /T /F`; POSIX group signaling remains portable; a real Windows parent/child integration test verifies forced cleanup
- Access tokens: show-once cryptographic tokens, HMAC-only SQLite persistence, metadata-only listing, permanent revocation, and atomic restricted `generated/api-keys.txt` rendering while the router is stopped
- OpenCode: generated configuration from live native model IDs with an environment-variable API-key placeholder; no direct modification of user configuration
- Local library: read-only `/api/library` projection includes only completed downloads whose paths remain inside the managed root and whose files still match expected sizes; sharded models resolve to the first shard
- Managed artifacts: stopped-router deletion resolves only durable download IDs inside the managed root, atomically renames before removal, preserves download provenance and profiles, and supports exact pinned re-download through the existing durable job
- Profile health: profile responses derive Available/Broken state and source download provenance; router startup rejects broken enabled profiles before writing/launching a stale preset
- Artifact deletion is group-scoped even when multiple download jobs share one repository/revision directory; profile health evaluates every matching provenance record and prefers a valid completed artifact, preventing stale duplicate jobs from marking healthy profiles Broken
- Download progress: active Hugging Face hashed/ETag-qualified incomplete files are discovered under each job's isolated local-dir cache, sampled every 250 ms, and persisted as monotonic byte counts while each file transfers
- Transfer interruption: each `hf_hub_download` call runs in an argument-vector child process; pause/cancel persist state, kill and reap the active transfer before returning, pause retains the isolated staging tree for Hub range resume, and cancel removes that tree after termination
- Runtime installation: `services/llama_release_installer.py` resolves stable release pointers through `nightly-tag.txt`, enumerates published assets, verifies SHA-256 digests, rejects unsafe archive paths, probes staged runtimes, and atomically promotes validated payloads; `/api/runtimes/releases/{tag}` and `/api/runtimes/install` expose the control-plane surface

## Implemented Frontend Surfaces

- React 19, TypeScript, Vite, TanStack Query, Lucide icons, and locally bundled DM Sans/IBM Plex Mono typography
- Responsive operations shell with persistent desktop navigation and eight-destination mobile navigation
- Live router, runtime, profile, access-key, and model state queries with periodic reconciliation
- Functional router start/stop/restart and native model load/unload controls with disabled and error states
- Model inventory, server process/runtime/log view, and persisted record views for profiles, runtimes, and access keys
- Runtime registration dialog with executable probing, backend selection, pending/error states, and ready-state inventory
- Basic/Advanced model profile editor with alias normalization, first-shard guidance, runtime selection, and capability-aware controls
- Access workspace with show-once in-memory key reveal, clipboard feedback, metadata-only listing, and confirmed revocation
- OpenCode configuration panel generated from live router model IDs with an environment-variable key placeholder
- Hugging Face catalog search with sorting, repository selection, exact quantization sizes, shard completeness, and explicit download creation
- Discover distinguishes remote shard availability (`All shards available`/`Missing shards`) from local state (`Downloaded`, `Queued`, `Downloading`, or `Paused`) using exact repository, revision, and group matches; validated library entries are authoritative for Downloaded and duplicate active/downloaded jobs are disabled
- Durable download workspace with progress, active-job count, pause/resume/cancel controls, errors, and active-state polling
- Models lists validated downloaded GGUF artifacts from `/api/library`, with repository/group/size/file/revision details, scoped refresh, Discover navigation, and Configure handoff into Profiles; Profiles remains the launch-configuration workspace
- Models supports confirmed artifact deletion while preserving profiles; Profiles marks missing managed models Broken, offers confirmed exact re-download repair, and supports confirmed profile-only deletion. Destructive actions require the router to be stopped
- A download transition to Completed immediately refreshes the validated library instead of waiting for its periodic polling interval
- Clear finished hides completed and cancelled jobs while preserving completed records for the local library
- Completed validated downloads expose a Configure action that opens profile creation with the local model, alias, and runtime prefilled
- Desktop and mobile layouts use stable metrics, table reduction, and fixed navigation without content overlap

## Important Constraints

- The downloaded-model library currently projects completed jobs; general scanning of externally added GGUF files remains deferred.
- Do not download the real 93.7 GB Qwen group during development or tests.
- Do not invoke executables through a shell; use argument vectors.
- The control plane supervises native `llama-server`; it does not proxy or reimplement inference.
- Non-loopback router binding requires an API key file.
- llama.cpp requires raw keys in `--api-key-file`; the current generated file is permission-restricted and Git-ignored but remains plaintext at rest. Move authoritative secrets to the OS credential store and materialize the file only for router launches during hardening.
- Downloaded groups remain in a hidden staging directory until all required files pass size validation, then publish through a same-filesystem atomic rename.
- `huggingface_hub.hf_hub_download` has no cancellation/progress callback. The app isolates each call in a child process so it can be terminated promptly without attempting unsafe Python thread cancellation.
- Keep unit tests deterministic and free of live network, fixed ports, installed llama.cpp, and large model dependencies.

## Local Assets and Acceptance Target

- Runtime: `runtime/llama-b11053-cpu-x64/llama-server.exe` (ignored by Git).
- Reported runtime build: `0.4.1-dev`, commit `1af554f8f`.
- Primary repository: `unsloth/Qwen3.8-Flash-Next-GGUF`.
- Pinned observed revision: `38bb39ee97821de2c9009abb7e93950eec396e66`.
- `UD-IQ4_XS`: 3 files totaling 93,682,584,224 bytes.
- No `.env` or Hugging Face token is currently configured; this repository is public.
- Current registered runtime is `Vulcan` at `E:\llama\llama-server.exe`; its probe reports no Vulkan devices on this machine.
- This test machine has an RTX 4090 and 128 GB RAM. Register a CUDA llama.cpp build before GPU acceptance.
- Recommended first real model: `unsloth/Qwen3.5-9B-GGUF`, revision `3885219b6810b007914f3a7950a8d1b469d598a5`, `Qwen3.5-9B-Q4_K_M`, one complete file, 5,680,522,464 bytes (5.29 GiB).
- CUDA runtime: `E:\llama-cuda-b11053\llama-server.exe`; supplied archive is actually build b11060 (`0.4.1-dev`, commit `426090367`) with CUDA 12.4 libraries. Probe reports `CUDA0: NVIDIA GeForce RTX 4090 (24563 MiB, 23036 MiB free)`.

## Next Implementation Slice

Add the runtime installer UI and backend asset selection, including CUDA companion asset grouping, then implement runtime switching/rollback. Do not use the 93.7 GB target for routine acceptance.

Manual acceptance for transfer interruption:

1. Start a model download and wait for its byte count to advance.
2. Select **Pause** and confirm the API returns Paused promptly, bytes stop increasing, and the hidden `.incomplete` file remains.
3. Select **Resume** and confirm progress continues from the retained byte count rather than restarting at zero.
4. Select **Cancel** and confirm the API returns Cancelled promptly, bytes stop increasing, the job-specific hidden staging directory is removed, and no destination is published.

Measured live transfer-interruption acceptance on 2026-09-20:

- Started `unsloth/Qwen3-Coder-Next-GGUF` revision `ce09c67b53bc8739eef83fe67b2f5d293c270632`, group `Qwen3-Coder-Next-UD-TQ1_0`, through the UI as job `e0d7181d-a0f8-4b59-b7dc-2ef147d2610b`.
- Before pause, API/filesystem samples increased from 125,829,120 to 209,715,200 to 262,144,000 to 293,601,280 bytes. Pause returned and rendered Paused in 50 ms; the retained incomplete file stopped at 576,716,800 bytes for at least 2.5 seconds, no transfer child remained, and no destination was published.
- Resume returned and rendered Downloading in 37 ms. The same incomplete path continued from the retained data and increased through 681,574,400, 713,031,680, 754,974,720, and 828,375,040 bytes rather than restarting at zero.
- Cancel returned and rendered Cancelled in 190 ms at 1,027,604,480 persisted bytes. After 1.5 seconds the job-specific staging directory and transfer child were absent, and no destination had been published.
- The live proof used the already-authorized large repository only long enough to test interruption; the transfer was cancelled and its new staging data removed.

Measured CUDA/router acceptance on 2026-09-20:

- Registered runtime `CUDA b11060` (`bc541c85-83d5-43f3-b641-1793fd6c9aef`) from the user-supplied CUDA 12.4 folder. Direct and managed probes both detected the RTX 4090; no runtime download was performed by the app.
- Created profile `qwen3.8-27b-cuda` (`4960148c-aacf-462e-b10e-87c2b21c493e`) against the already validated 13.3 GiB `unsloth/Qwen3.8-27B-GGUF` `UD-IQ4_XS` artifact, with 60 GPU layers, 32,768 context, and flash attention on.
- The managed router reached Ready in router mode and advertised exactly one preset. Native load reached Loaded; metadata reported 27,320,697,856 parameters, 32,768 active context, and `IQ4_XS - 4.25 bpw`. The worker process was `E:\llama-cuda-b11053\llama-server.exe`.
- A random bearer token was rejected with 401. Using the existing local key without printing it, authenticated `/v1/models` returned `qwen3.8-27b-cuda`; a non-streaming chat request completed at the protocol level, and a streaming request produced 19 SSE events plus `[DONE]` at 20.66 predicted tokens/s. The low token cap was consumed by reasoning, so exact visible response text remains to be accepted with a larger output budget or reasoning disabled.
- Generated OpenCode configuration contained `qwen3.8-27b-cuda` and the `LLAMA_WEB_UI_API_KEY` environment placeholder, with no raw `lwui_` token. The model was unloaded and router stopped after acceptance.
- Authenticated non-streaming chat returned visible `CONNECTION_OK`; authenticated streaming returned visible `STREAM_OK` and `[DONE]`; a function-tool request returned the parseable `get_weather` call with `{"city":"Paris"}`. The model was unloaded and router stopped after acceptance.

Manual acceptance for terminal-job clearing:

1. Open Downloads and confirm completed or cancelled jobs are visible.
2. Select **Clear finished** beside the active count.
3. Confirm completed and cancelled rows disappear while failed, paused, queued, and downloading rows remain.
4. Confirm any downloaded model from a cleared completed job remains available when creating a profile.

Manual acceptance for in-file progress:

1. Start a model download and open Downloads.
2. Confirm the byte count and progress bar advance before the current GGUF file completes.
3. Refresh the page during transfer and confirm the latest persisted progress remains visible.
4. Confirm the job reaches 100% and Completed after validation and atomic publication.

Measured real-transfer acceptance on 2026-09-20:

- Root cause: `huggingface_hub` 0.36.2 does not write `<filename>.incomplete` for `local_dir` downloads. It derives `.cache/huggingface/download/<base64-sha1(metadata-name)>.<etag>.incomplete`, so the previous exact-path sampler never saw active bytes.
- The authorized repository `unsloth/Qwen3-Coder-Next-GGUF` at revision `ce09c67b53bc8739eef83fe67b2f5d293c270632` exposed 35 complete groups. The smallest was `Qwen3-Coder-Next-UD-TQ1_0`, one file totaling 18,941,835,296 bytes (17.6 GiB), and it was started through the Discover UI.
- For job `3d7b3d27-c9a9-4ced-8d8d-43ef7847e4b2`, active bytes were written to `data/models/unsloth/Qwen3-Coder-Next-GGUF/.ce09c67b53bc8739eef83fe67b2f5d293c270632.3d7b3d27-c9a9-4ced-8d8d-43ef7847e4b2.partial/.cache/huggingface/download/b7exlr1Rr7qLDLwcAZdupd92-vQ=.352049921f1dc861bf33fd03f5d2c25a08f248ee808179fe63ce16307357551c.incomplete`.
- Four pre-completion API/filesystem samples matched and increased from 1,583,349,760 to 1,646,264,320 to 1,740,636,160 to 1,782,579,200 bytes. The Downloads UI showed 2.4 GiB / 17.6 GiB (13%), then 3.0 GiB (17%), and 3.6 GiB (21%) before cancellation.
- The job was cancelled after proof, at a persisted 3,911,188,480 bytes. No destination was published. This observation preceded the child-process transfer implementation; the later measured interruption acceptance above supersedes the old file-boundary cancellation behavior.
- Full backend validation passed: 146 tests, 93.29% coverage, Ruff, and strict mypy for 38 source files. Frontend files were unchanged, so frontend tests/build and responsive re-acceptance were not required.

## Resume State

- Commit `69c4fd5` is the checked-in Discover/Downloads baseline on `alpha` and `origin/alpha`.
- Root `data/` is ignored because it contains local runtime/application state. Commit `1f8eca6` removed `data/llamawebui.db` from Git tracking only; the local file remains intact and must not be deleted or reset.
- 151 backend and 18 frontend tests pass; Ruff, strict mypy, production build, and editor diagnostics pass.
- Live public-catalog acceptance returned 25 results and 27 complete groups for the selected repository; desktop and 390x844 layouts had no horizontal overflow. No download job was created.
- At handoff, the backend is healthy on `http://127.0.0.1:18080/api/health` and Vite is serving `http://127.0.0.1:5173/`.
- Exact next slice: finish authenticated connection acceptance with visible non-streaming output, streaming content, and a simple parseable tool call; then implement official llama.cpp release discovery/install (including CUDA companion assets and digest verification). The recommended 5.29 GiB model download is no longer required to prove basic CUDA operation because an existing validated model loaded and streamed successfully.

Deferred backend work includes real-runtime SSE/inference acceptance, local library scanning, release installation, broader event publication, and authenticated connection tests.
