# LlamaWebUI Implementation Progress

**Updated:** 2026-09-19
**Branch:** `alpha`
**Baseline commit:** `6fe8dc2` (`feat: add router lifecycle supervision foundation`)
**Active phase:** Phase 4 - Server lifecycle

This is the session handoff document. Update it after each completed implementation slice. The authoritative requirements remain in [llama-web-ui-plan.md](llama-web-ui-plan.md).

## Current Position

The delivery plan has eight numbered phases (`0` through `7`). Work has intentionally not been strictly sequential: the durable Hugging Face download core from Phase 5 was implemented before router supervision in Phase 4.

| Phase | Status | Implemented | Important remaining work |
| --- | --- | --- | --- |
| 0. Feasibility spikes | Partial | Real llama.cpp b11053 probed; required Qwen flags confirmed; generated preset accepted; `/v1/models` returned the test alias | Real load/inference/SSE/tool tests, API-key reload behavior, Windows worker-tree shutdown, older-build comparison |
| 1. Application foundation | Partial | Python package, FastAPI, settings, SQLite, Alembic, portable data directory | Event channel, structured/redacted logging, single-instance lock, diagnostics, frontend/static packaging |
| 2. Runtime manager | Partial | Register/list/get/reprobe/remove; option/device capability parsing; in-use deletion guard | GitHub release discovery/install, stable-to-build resolution, digest verification, switching/rollback |
| 3. Local library and profiles | Partial | Profile persistence, typed Qwen options, capability validation, shard completeness, deterministic atomic preset writing | Directory scanning, GGUF metadata, logical model records, command import/export, combined active preset |
| 4. Server lifecycle | In progress | Router state machine, validated argument vector, process-group launch, single-process supervisor, HTTP readiness polling, bounded log tail, crash observation, bounded stop/kill | App/API wiring, combined preset, persistent runs, restart policy, port ownership, native model operations, Windows process-tree proof |
| 5. Hugging Face and downloads | Advanced partial | Search, repository manifests, quant/shard grouping, revision-pinned durable jobs, staging, size verification, atomic publication, pause/resume/cancel coordination, startup reconciliation | In-file progress, retry/backoff, periodic disk checks, checksum/ETag verification, event streaming, projector association, safe deletion, library reconciliation |
| 6. Tokens and onboarding | Not started | None | Token metadata/key file, lifecycle integration, OpenCode generation, authenticated connection tests |
| 7. Hardening and release | Not started | Unit quality gate established | Packaging, CI/platform matrix, accessibility, backup/restore, offline behavior, operator docs |

No phase has met its complete release gate yet because each gate includes later integration, frontend, packaging, or real-runtime acceptance work.

## Verified Baseline

From `backend/` using `..\.venv\Scripts\python.exe`:

```powershell
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m mypy src/llamawebui
```

Last verified at commit `6fe8dc2`:

- 73 tests passed.
- 95.72% total coverage; configured floor is 90% with branch coverage enabled.
- Ruff passed.
- Strict mypy passed for 27 source files.
- Two dependency deprecation warnings remain: Starlette/httpx and AnyIO `BlockingPortal`.

Current working tree after the readiness/log-capture slice:

- 75 tests passed.
- 94.66% total coverage.
- Ruff, strict mypy, and `git diff --check` passed.

## Implemented Backend Surfaces

- Configuration and startup: `backend/src/llamawebui/config.py`, `app.py`, `__main__.py`
- Persistence: `database.py`, `models.py`, Alembic migrations `0001` through `0003`
- Runtime capability and registry: `domain/runtime_capabilities.py`, `services/runtime_probe.py`, `services/runtime_registry.py`
- Profiles/presets: `domain/model_profile.py`, `services/profile_registry.py`
- Hugging Face discovery: `domain/model_manifest.py`, `services/huggingface_catalog.py`
- Downloads: `domain/download_job.py`, `services/download_registry.py`, `services/download_worker.py`, `services/download_coordinator.py`
- Router foundation: `domain/router_lifecycle.py`, `services/router_supervisor.py`

## Important Constraints

- Continue backend work unless the user explicitly redirects to frontend work.
- Do not download the real 93.7 GB Qwen group during development or tests.
- Do not invoke executables through a shell; use argument vectors.
- The control plane supervises native `llama-server`; it does not proxy or reimplement inference.
- Non-loopback router binding requires an API key file.
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

1. Generate one combined active router preset from enabled profiles sharing a selected runtime.
2. Wire `GET /api/server/status` and start/stop endpoints into FastAPI using injected supervisor dependencies.
3. Add API tests using fake process and health adapters before any real-runtime smoke test.
4. Persist server run attempts and terminal outcomes.

After that, add persistent `ServerRun` records and migration `0004`, bounded restart policy, port ownership checks, and Windows process-tree termination verification.
