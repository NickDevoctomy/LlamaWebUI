# Phase 5.1 — General library reconciliation

**Status:** Complete  
**Date:** 2026-09-26

## Implementation

- Made unmanaged GGUF discovery directory-scoped and case-insensitive by grouping shard candidates by canonical parent directory and filename stem. Identically named models in separate repositories/directories no longer combine into one incomplete or duplicate model.
- Made logical-model reconciliation and profile-link reconciliation use a normalized, platform-aware canonical comparison key while preserving the original resolved path for display and persistence.
- Preserved existing behavior for missing logical identities, profile links, downloaded provenance, and unmanaged files. No deletion or migration of model files was added.

Changed files:

- `backend/src/llamawebui/services/model_library.py`
- `backend/src/llamawebui/services/logical_model_registry.py`
- `backend/tests/test_model_library.py`

## Tests

- Added `test_library_discovery_keeps_same_named_models_in_separate_directories`, proving two complete same-named sharded GGUF sets remain distinct.
- Existing model-library and artifact-registry tests cover completed-download projection, checksum/size validation, duplicate completed-job projection, external shard discovery, missing identity retention/repair/removal, profile linking, provenance preservation, and safe artifact deletion.
- Tests use temporary directories and SQLite databases only; no network, secrets, fixed ports, llama-server process, or large download is required.

Focused command:

- `backend\\..\\.venv\\Scripts\\python.exe -m pytest tests/test_model_library.py tests/test_model_artifact_registry.py` from `backend/`: **29 passed** in 6.36s. The focused run's repository-wide 85% coverage gate was not applicable to a subset and reported 41.97%; this was followed by the complete gate below.

## Validation

- Full backend pytest: **281 passed, 1 skipped**, 90.42% branch coverage, above the configured 85% threshold. Start `2026-09-26T08:49:06.3789314Z`; end `2026-09-26T08:49:47.5281140Z`; elapsed 41.15s.
- Ruff: passed.
- Strict mypy: passed for 50 source files.
- Frontend: **27 tests passed**; production `npm run build` passed.
- `git diff --check`: passed.
- Final status contains only the three intended source/test files; no generated machine state or secrets were added.

## Manual acceptance

No browser, runtime, process, or live-upstream acceptance was required for this deterministic reconciliation-only slice.

## Next action

Begin Phase 5.2 — offline and upstream-failure behavior. Do not begin Phase 5.3 until the Phase 5.2 slice is complete.

## Suggested commit message

`fix: reconcile duplicate local model provenance safely`
