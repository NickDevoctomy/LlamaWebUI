# Phase 5.2 — Offline and upstream-failure behavior

**Status:** Complete  
**Date:** 2026-09-26

## Implementation

- Added bounded in-memory caching to `HuggingFaceCatalog` for search results and repository manifests, keyed by query/sort/limit or repository/revision.
- Cached metadata is returned during transient Hub failures while it remains within the configured five-minute TTL.
- Uncached Hub/network failures now raise a non-secret `CatalogUnavailableError` instead of exposing provider details.
- Control-plane Hugging Face search, repository, and download planning endpoints return HTTP 503 with an explicit unavailable message when no cached metadata exists.
- Existing 404 handling and redacted upstream error behavior remain unchanged.

Files changed:

- `backend/src/llamawebui/services/huggingface_catalog.py`
- `backend/src/llamawebui/app.py`
- `backend/tests/test_huggingface_catalog.py`
- `backend/tests/test_huggingface_api.py`

## Tests

- Added deterministic cached-search behavior when the provider becomes unavailable.
- Added deterministic uncached offline failure behavior.
- Added API coverage proving offline failures become 503 responses with safe details.
- Existing tests continue to cover provider 404/401-style failures, redaction, revision-pinned repository metadata, missing README handling, download retry exhaustion behavior, and local download verification.
- All tests use mocks, temporary directories, and temporary SQLite databases; no live Hub/GitHub service, secret, fixed port, llama-server process, or large download is required.

Focused validation:

- Hugging Face catalog/API tests: **11 passed**. The focused pytest invocation reported the expected repository-wide coverage failure because it intentionally exercised only two test modules; the complete gate below passed.

## Validation

- Full backend pytest: **284 passed, 1 skipped**, 90.24% branch coverage, above the configured 85% threshold.
- Ruff: passed.
- Strict mypy: passed for 50 source files.
- Frontend: **27 tests passed**; production build passed.
- `git diff --check`: passed.
- No generated files, secrets, or machine-state files were added.

## Manual acceptance

No browser, runtime, process, or live-upstream acceptance was required for this deterministic offline/failure-handling slice.

## Next action

Begin Phase 5.3 — primary download acceptance using only the approved small acceptance model. Do not begin Phase 6 until the Phase 5 gate passes.

## Suggested commit message

`feat: preserve cached hub metadata during outages`
