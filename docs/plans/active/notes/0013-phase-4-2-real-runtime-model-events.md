# Phase 4.2 - Real-runtime model-event capture

**Status:** Complete  
**Date:** 2026-09-24  
**Suggested commit:** `test: record native model lifecycle events`

## Implementation summary

- No application implementation changed. This slice captured native events from the existing managed llama.cpp runtime.
- Used registered CUDA runtime `b11060` (`0.4.1-dev`) and the already validated, enabled `qwen3.5-9b` profile.
- Captured complete native `status_change` SSE frames for both load and unload. The load frame reported `loading`; the unload frame included `status` and `exit_code` data fields. The control API independently reported the model as `loaded` after load and `unloaded` after unload.
- The profile began and ended unloaded. The managed router was stopped after capture; no profile, token, model file, or runtime registration was changed. The development control plane and frontend were left running for operator use.
- No secret-bearing headers or raw frame contents were saved. No download, inference request, or token operation was performed.

## Tests

- Focused deterministic gate: `python -m pytest --no-cov tests/test_router_client.py tests/test_server_api.py tests/test_router_event_sync.py` — **38 passed** (3.41 seconds; 2 dependency deprecation warnings).
- These tests mock HTTP/SSE behavior and do not require live upstream services, secrets, fixed ports, installed llama-server processes, or large downloads.

## Live acceptance evidence

- Control plane health check passed on `127.0.0.1:18080`.
- Router started through the managed API with runtime build `0.4.1-dev` (`b11060`) and reached `ready` (PID `3124`).
- Before each operation, a bounded reader was attached to `/api/server/models/events`.
- Load of `qwen3.5-9b` emitted a complete `status_change` event with status `loading`; the native model list subsequently showed `loaded`.
- Unload emitted a complete `status_change` event with `status` and `exit_code` fields; the native model list subsequently showed `unloaded`.
- Durable run history reported the latest run `stopped` with no error. The router had no PID and port `1234` had no listener after managed stop.
- The native runtime therefore emits model status events for both load and unload transitions. Existing deterministic SSE tests remain the routine gate.

## Manual acceptance record

1. Start the local control plane and frontend using the repository's documented local setup; both loopback services became available.
2. Start registered runtime `b11060` using the managed control API; verify router state `ready`.
3. Attach a bounded SSE reader to `/api/server/models/events`, load existing profile `qwen3.5-9b`, and observe the native `status_change` event.
4. Attach a bounded SSE reader again, unload the profile, and observe the native `status_change` event.
5. Verify the profile returned to `unloaded`, stop the managed router, and verify durable run state `stopped`, no router PID, and no listener on port `1234`.

No failure symptoms were observed. The user did not need to perform a secret-creating or destructive action.

## Validation

- Focused model-event tests: **38 passed**, 3.41 seconds.
- No backend or frontend source changed; full backend/frontend quality gates were not rerun for this live-acceptance/documentation slice.
- `git diff --check`: passed.

## Next action

Begin Phase 5 slice 5.1 - general library reconciliation. Do not begin Phase 6 until the Phase 5 gate passes.