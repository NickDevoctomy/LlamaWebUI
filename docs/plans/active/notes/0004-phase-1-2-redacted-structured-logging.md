# Phase 1.2 — Redacted structured logging

**Status:** Complete  
**Date:** 2026-09-21  
**Suggested commit:** `feat: add redacted structured logging`

## Implementation summary

Added application-owned structured JSON logging with UTC timestamps, log levels, component/event fields, and redaction of bearer tokens, `hf_`/`lwui_` token-shaped values, configured secret values, and sensitive token environment assignments.

Changed files:

- `backend/src/llamawebui/services/logging_utils.py`
- `backend/src/llamawebui/config.py`
- `backend/src/llamawebui/__main__.py`
- `backend/src/llamawebui/app.py`
- `backend/src/llamawebui/services/router_supervisor.py`
- `backend/src/llamawebui/services/download_coordinator.py`
- `backend/tests/test_logging_utils.py`

Application startup/shutdown, router state/output, and download-start events now retain useful lifecycle context without logging raw launch arguments, authorization headers, token values, or key-file contents. Router output is redacted before both in-memory retention and debug logging. The development probe command continues to emit machine-readable JSON on stdout.

No database, model, token, runtime, or frontend state was changed.

## Tests and validation

- Focused tests: **26 passed** across logging, router supervisor, and download coordinator tests. The focused command's repository-wide coverage threshold failure is expected because it intentionally omits most test modules; no focused test failed.
- Full backend tests: **232 passed** in **18.53 seconds**; **90.18% branch coverage**, meeting the configured 90% threshold.
- Ruff: passed.
- Strict mypy: passed.
- Frontend tests: **19 passed**.
- Frontend production build: passed.
- `git diff --check`: passed.
- Tests are deterministic and do not require live upstream services, secrets, fixed ports, installed llama-server processes, or model downloads.

## Manual acceptance

No browser or runtime acceptance was required. The change is covered by deterministic emitted-record and router-log-tail redaction tests.

## Next action

Begin Phase 1 slice 1.3 — diagnostics bundle. Do not begin slice 1.4 until slice 1.3 passes all required gates.
