# LlamaWebUI Implementation Progress

**Updated:** 2026-09-20
**Branch:** `alpha`
**Baseline commit:** `1f8eca6` (`feat: add access and OpenCode workflows`)
**Active phase:** Frontend implementation

This is the session handoff document. Update it after each completed implementation slice. The authoritative requirements remain in [llama-web-ui-plan.md](llama-web-ui-plan.md).

## Current Position

The delivery plan has eight numbered phases (`0` through `7`). Work has intentionally not been strictly sequential: the durable Hugging Face download core from Phase 5 was implemented before router supervision in Phase 4.

| Phase | Status | Implemented | Important remaining work |
| --- | --- | --- | --- |
| 0. Feasibility spikes | Partial | Real llama.cpp b11053 probed; required Qwen flags confirmed; generated preset accepted; `/v1/models` returned the test alias; Windows parent/child process-tree shutdown verified | Real load/inference/SSE/tool tests, API-key reload behavior, older-build comparison |
| 1. Application foundation | Advanced partial | Python package, FastAPI, settings, SQLite, Alembic, portable data directory, unified bounded event broker with sequencing/replay/reconciliation, React/Vite operator shell | Publish remaining download/runtime/profile state changes, structured/redacted logging, single-instance lock, diagnostics, static frontend packaging |
| 2. Runtime manager | Partial | Register/list/get/reprobe/remove; option/device capability parsing; in-use deletion guard | GitHub release discovery/install, stable-to-build resolution, digest verification, switching/rollback |
| 3. Local library and profiles | Partial | Profile persistence, typed Qwen options, capability validation, shard completeness, deterministic atomic single/combined preset writing | Directory scanning, GGUF metadata, logical model records, command import/export |
| 4. Server lifecycle | In progress | Router state machine, validated argument vector, process-group launch, single-process supervisor, HTTP readiness polling, bounded log tail, crash observation, owned process-tree graceful/forced shutdown, serialized status/start/stop/restart API, durable run and restart-attempt history, safe port preflight, bounded crash recovery with rapid-failure suppression, native model list/load/unload/SSE APIs, lifecycle/model event publication through unified replayable `/api/events` | Real-runtime SSE acceptance |
| 5. Hugging Face and downloads | Advanced partial | Search, repository manifests, quant/shard grouping, revision-pinned durable jobs, staging, size verification, atomic publication, pause/resume/cancel coordination, startup reconciliation, responsive Discover and Downloads workflows | In-file progress, retry/backoff, periodic disk checks, checksum/ETag verification, event streaming, projector association, safe deletion, library reconciliation |
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

Backend baseline before the current frontend-only slice:

- 141 tests passed.
- 94.35% total coverage; configured floor is 90% with branch coverage enabled.
- Ruff passed.
- Strict mypy passed for 35 source files.
- Two dependency deprecation warnings remain: Starlette/httpx and AnyIO `BlockingPortal`.

Frontend foundation validation:

- Vite production build passed; JavaScript bundle is 308.81 kB.
- Vitest/Testing Library passed: 8 component integration tests.
- Desktop 1440x1000 and mobile 390x844 browser checks passed without horizontal overflow.
- Live FastAPI queries and responsive navigation were verified in the browser.
- Runtime registration was accepted end to end against local llama.cpp b11053 and displayed build `0.4.1-dev` as ready.

## Implemented Backend Surfaces

- Configuration and startup: `backend/src/llamawebui/config.py`, `app.py`, `__main__.py`
- Persistence: `database.py`, `models.py`, Alembic migrations `0001` through `0005`
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
- Durable download workspace with progress, active-job count, pause/resume/cancel controls, errors, and active-state polling
- Desktop and mobile layouts use stable metrics, table reduction, and fixed navigation without content overlap

## Important Constraints

- The backend APIs for discovery and downloads exist, but their frontend destinations are placeholders.
- Do not download the real 93.7 GB Qwen group during development or tests.
- Do not invoke executables through a shell; use argument vectors.
- The control plane supervises native `llama-server`; it does not proxy or reimplement inference.
- Non-loopback router binding requires an API key file.
- llama.cpp requires raw keys in `--api-key-file`; the current generated file is permission-restricted and Git-ignored but remains plaintext at rest. Move authoritative secrets to the OS credential store and materialize the file only for router launches during hardening.
- Downloaded groups remain in a hidden staging directory until all required files pass size validation, then publish through a same-filesystem atomic rename.
- `huggingface_hub.hf_hub_download` has no cancellation/progress callback. Active cancellation is durable immediately, but the current SDK call can stop only at a file boundary.
- Keep unit tests deterministic and free of live network, fixed ports, installed llama.cpp, and large model dependencies.

## Local Assets and Acceptance Target

- Runtime: `runtime/llama-b11053-cpu-x64/llama-server.exe` (ignored by Git).
- Reported runtime build: `0.4.1-dev`, commit `1af554f8f`.
- Primary repository: `unsloth/Qwen3.8-Flash-Next-GGUF`.
- Pinned observed revision: `38bb39ee97821de2c9009abb7e93950eec396e66`.
- `UD-IQ4_XS`: 3 files totaling 93,682,584,224 bytes.
- No `.env` or Hugging Face token is currently configured; this repository is public.

## Next Implementation Slice

Build the local-library bridge: reconcile completed downloads into logical local models, expose their validated first-shard paths in the UI, and prefill profile creation from a selected artifact. This closes the remaining fresh-install gap between downloading a model and starting the router.

For development acceptance, use a tiny test GGUF or a mocked completed download; do not download the 93.7 GB target model. Once a model is available, create its profile, start the router, verify the live model ID, then verify generated OpenCode configuration and authenticated access.

## Resume State

- Commit `1f8eca6` is the checked-in Access/OpenCode baseline on `alpha` and `origin/alpha`.
- The Access/OpenCode frontend slice and root `data/` ignore rule are committed.
- Root `data/` is ignored because it contains local runtime/application state. Commit `1f8eca6` removed `data/llamawebui.db` from Git tracking only; the local file remains intact and must not be deleted or reset.
- Eight frontend tests pass, the production build passes, editor diagnostics are clean, and `git diff --check` passes.
- Live public-catalog acceptance returned 25 results and 27 complete groups for the selected repository; desktop and 390x844 layouts had no horizontal overflow. No download job was created.
- At handoff, the backend is healthy on `http://127.0.0.1:18080/api/health` and Vite is serving `http://127.0.0.1:5173/`.
- First action next session: implement the local-library/profile bridge for completed downloads.

Deferred backend work includes real-runtime SSE/inference acceptance, local library scanning, release installation, broader event publication, and authenticated connection tests.
