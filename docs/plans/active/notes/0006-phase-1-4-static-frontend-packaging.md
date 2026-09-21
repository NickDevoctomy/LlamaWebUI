# Phase 1.4 — Static frontend packaging

**Status:** Complete  
**Date:** 2026-09-21  
**Suggested commit:** `feat: serve packaged frontend assets`

## Implementation summary

Configured the Vite production build to emit frontend assets into `backend/src/llamawebui/static`, included that directory in the Python wheel, and mounted it from FastAPI when available. API routes remain available under `/api`, and development Vite proxy behavior is unchanged.

Changed files:

- `frontend/vite.config.ts`
- `backend/pyproject.toml`
- `backend/src/llamawebui/app.py`
- `backend/tests/test_app.py`
- generated package-controlled assets under `backend/src/llamawebui/static/`

`create_app` accepts an optional static directory for deterministic packaged-mode testing; the default resolves beside the Python package. A temporary static directory smoke test verifies that `/` serves `index.html` while `/api/health` remains reachable. No runtime, database, model, token, or development-server behavior was changed.

## Tests and validation

- Focused packaged-static smoke test: passed within `tests/test_app.py`.
- Full backend tests: **237 passed**; **90.07% branch coverage**, meeting the configured 90% threshold. Timed run: start `2026-09-21T16:45:00.1787599+01:00`, end `2026-09-21T16:45:20.0899985+01:00`, elapsed **19.91 seconds**.
- Ruff: passed.
- Strict mypy: passed.
- Frontend tests: **19 passed**.
- Frontend production build: passed and emitted assets to `backend/src/llamawebui/static`.
- `git diff --check`: passed.
- Tests are deterministic and do not require live upstream services, secrets, fixed ports, installed llama-server processes, or model downloads.

## Manual acceptance

No separate browser acceptance was required. The packaged-mode HTTP smoke test exercises the served HTML and confirms API routing remains functional.

## Next action

Phase 1 is ready for its gate review. Do not begin Phase 2 until the Phase 1 gate is explicitly marked complete.
