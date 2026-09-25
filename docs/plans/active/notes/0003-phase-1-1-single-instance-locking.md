# Phase 1.1 — Single-instance locking

**Status:** Complete  
**Date:** 2026-09-21  
**Suggested commit:** `feat: add single-instance application locking`

## Implementation summary

Added `InstanceLock` under the selected application data directory. The CLI acquires `llamawebui.lock` before starting Uvicorn and releases it on clean shutdown. Ownership metadata is kept in a sidecar and includes the process ID and creation time. A competing process receives a clear `InstanceAlreadyRunningError`; unverified lock ownership is never deleted or stolen.

Changed files:

- `backend/src/llamawebui/services/instance_lock.py`
- `backend/src/llamawebui/__main__.py`
- `backend/tests/test_instance_lock.py`
- `backend/tests/test_cli.py`

No database, model, token, runtime, or frontend state was changed.

## Tests and validation

- Focused tests: **7 passed** (`test_instance_lock.py`, `test_cli.py`).
- Full backend tests: **229 passed**; **90.00% branch coverage**, meeting the configured 90% threshold.
- Ruff: passed.
- Strict mypy: passed.
- `git diff --check`: passed.
- Tests are deterministic and do not require live upstream services, secrets, fixed ports, installed llama-server processes, or model downloads.

## Manual acceptance

No browser or runtime acceptance was required. A stale LlamaWebUI process holding port `18080` was terminated before validation.

## Next action

Begin Phase 1 slice 1.2 — redacted structured logging. Do not begin later Phase 1 slices until 1.2 passes all required gates.