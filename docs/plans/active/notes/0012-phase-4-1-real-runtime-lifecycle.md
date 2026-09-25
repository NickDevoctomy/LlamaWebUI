# Phase 4.1 - Real-runtime lifecycle acceptance

**Status:** Complete — required lifecycle acceptance passed; inference confirmed by both OpenCode (user) and bounded authenticated API request  
**Date:** 2026-09-24  
**Suggested commit:** `test: record Qwen3.5-9B lifecycle acceptance`

## Setup and safety

- Used `start.ps1` as documented; backend and frontend were available at `127.0.0.1:18080` and `127.0.0.1:5173` with the Vite API proxy functioning.
- Existing Qwen3.5-9B profile `qwen3.5-9b` was enabled, marked available, and bound to the registered CUDA runtime `b11060` (`0.4.1-dev`). Its existing local GGUF is `Qwen3.5-9B-UD-IQ3_XXS.gguf`, 3.74 GiB, at the application-managed pinned model path.
- No profile edits, token creation, downloads, or model-file changes were made. The existing `LLAMA_WEB_UI_API_KEY` environment variable was used in-memory for authenticated native API requests; its value was not printed, written to files, or included in command text.
- User had already verified inference for this profile using OpenCode. The API inference check below also confirmed an HTTP 200 completion. One exploratory prompt with too small a token budget returned empty visible content; a later bounded prompt used adequate budget and finished successfully.

## Observed lifecycle

- Began from router state `stopped`, with no `llama-server.exe` process or listener on port `1234`.
- Cold start using registered CUDA runtime `CUDA b11060`, build `0.4.1-dev`, reached `ready` at `http://127.0.0.1:1234` (PID `35196`).
- Target profile began `unloaded`. Explicit load was acknowledged and transitioned `loading` → `loaded`.
- Authenticated native `GET /v1/models` returned HTTP 200 and included `qwen3.5-9b` (3 models total).
- Authenticated short chat completion returned HTTP 200 for model `qwen3.5-9b`; user independently confirmed the profile already worked through OpenCode. No reasoning-specific behavior is part of Phase 4.1.
- Explicit unload was acknowledged and reached `unloaded` while router remained `ready`.
- Managed restart reached `ready` with a different PID (`27408`); explicit reload transitioned `loading` → `loaded`; authenticated `/v1/models` again returned HTTP 200 with the target profile present.
- Managed stop transitioned `ready` → `stopped`; latest durable run row has `state=stopped`, no error, and exit code `3221225786` (`0xC000013A`). The router listener was absent and no managed `llama-server.exe` remained. The code is the Windows Ctrl+C/console-control termination status produced by the supervisor's non-forced `CTRL_BREAK_EVENT`; it is not evidence of a surviving/orphan process. The persisted state and actual process/port checks establish successful cleanup.
- App backend and Vite frontend were stopped after acceptance. Ports `1234`, `18080`, and `5173` were verified clear.

## Acceptance result

- All Phase 4.1 operations passed: start from stopped, readiness, authenticated model listing, model load, inference, unload, restart, stop, and orphan/listener checks.
- The stop exit code was assessed alongside supervisor state, durable run state/error, process ownership, and port release. It did not leave an orphan or occupied managed port; treat it as the expected Windows control-event termination code for the supervised process.

## Validation classification

- Existing automated lifecycle tests remain the deterministic routine gate; no source files were modified in this acceptance attempt.
- The first brief API call used too little output budget and returned no visible text; a later bounded request returned HTTP 200. User had already verified inference through OpenCode. Reasoning-specific behavior was not part of this slice.
- No token was created or exposed; the pre-existing environment variable was used without echoing it.

## Safe cleanup

- Managed router stop completed; port `1234` was free afterward.
- Backend/frontend processes were stopped after the run; ports `18080` and `5173` were free.
- Existing models, profiles, keys, database, and runtime registrations were left untouched.

## Next action

Begin Phase 4.2 - real-runtime model-event capture. Do not begin Phase 5 until the Phase 4 gate passes.
