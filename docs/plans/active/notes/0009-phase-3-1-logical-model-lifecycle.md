# Phase 3.1 - Logical-model lifecycle completion

**Status:** Complete  
**Date:** 2026-09-23  
**Suggested commit:** `test: complete logical model lifecycle coverage`

## Implementation

- Added deterministic coverage for preserving a logical-model ID and restoring its `valid` state when a missing model returns at the same canonical path.
- Added guard coverage proving valid records and missing records linked to profiles cannot be removed.
- Added API coverage for logical-record DELETE 404/409/204 outcomes and profile deletion unlinking a missing logical record before safe removal.
- The Models reconciliation summary now reports missing and profile-linked logical-model counts. The logical library table continues to show valid/missing status, profile counts, and the disabled removal control for linked missing records.
- Changed only app formatting in `backend/src/llamawebui/app.py` to satisfy Ruff's existing line-length violation found during the full gate.
- No new library abstraction or explicit unlink API was added: profile relationships remain derived from profile paths and are reconciled from durable profile state. Model files are never touched by logical-record removal.

## Tests

- Focused backend: `..\.venv\Scripts\python.exe -m pytest tests/test_model_library.py --no-cov` — 17 passed.
- Added same-path recovery, valid/linked removal rejection, API guard responses, profile deletion unlink, and final reconciliation count assertions.
- Added frontend tests for valid/missing display, linked counts, removable/disabled actions, and missing/linked reconciliation totals.
- All tests use temporary paths, fake runtime probing, and mocked browser fetch responses. They require no live upstream service, secrets, fixed ports, installed llama-server process, or large downloads.

## Validation

- Full backend: `..\.venv\Scripts\python.exe -m pytest` — 246 passed; branch coverage 90.30% (configured floor 90%). Final timed gate start `2026-09-23T12:43:08.4298249Z`, end `2026-09-23T12:43:37.2688123Z`, elapsed 28.839s for combined backend/frontend/diff checks; backend pytest itself completed in 20.58s.
- Ruff: `..\.venv\Scripts\python.exe -m ruff check .` — passed.
- Strict mypy: `..\.venv\Scripts\python.exe -m mypy src/llamawebui` — passed for 48 source files.
- Frontend: `npm test -- --run` — 22 passed.
- Production build: `npm run build` — passed.
- `git diff --check` — passed; Git emitted a line-ending warning for generated static CSS during the build.
- The production build refreshed the packaged static `index.html` and hashed JS bundle so packaged mode serves the updated Models workspace. Keep the HTML and matching hashed bundle together.

## Manual acceptance

1. Used an isolated data directory under `temp/phase-3-1-browser`; the Models page loaded in the browser and displayed the empty logical-library state and its explanatory text.
2. No model files, downloads, tokens, or existing user database were changed. The development services were left running.

## Next action

Begin Phase 3.2 - profile round-trip acceptance. Do not begin Phase 4 until the Phase 3 gate passes.
