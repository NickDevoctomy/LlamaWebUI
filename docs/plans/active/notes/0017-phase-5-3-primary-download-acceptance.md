# Phase 5.3 — Primary download acceptance

**Status:** Complete  
**Date:** 2026-09-26

## Implementation

- Fixed a Windows download-progress race where a Hugging Face `.incomplete` cache file disappeared during finalization and caused `WinError 2`.
- Made publication idempotent: if a validated destination was published before the durable state update, retry recognizes the complete artifact and transitions the job to `completed` without re-downloading.
- Allowed atomic replacement of a stale cache-only managed destination while continuing to reject empty or unexpected pre-existing destinations.
- Added regression tests for the cache-file race, stale cache-only publication, and idempotent completion after an already-published destination.
- Added a persistent active-plan rule requiring at most one LlamaWebUI backend/frontend development pair at a time.
- Deliberately did not modify unmanaged model files or expose the Hugging Face token.

Files changed:

- `backend/src/llamawebui/services/download_worker.py`
- `backend/tests/test_download_worker.py`
- `docs/plans/active/README.md`

## Live acceptance

Approved artifact:

- Repository: `unsloth/Qwen3.5-9B-GGUF`
- Revision: `3885219b6810b007914f3a7950a8d1b469d598a5`
- Group: `Qwen3.5-9B-Q4_K_M`
- Expected size: `5,680,522,464` bytes (5.29 GiB)

Observed sequence:

1. Started one documented local service pair and authenticated with the existing local account.
2. Searched Hugging Face, selected the approved repository and `Q4_K_M` group, and explicitly started the download.
3. Download reached approximately 99% and failed because the `.incomplete` cache file disappeared during finalization. This was diagnosed as a local race, not authentication; the repository metadata and transfer had succeeded.
4. The downloader fix was applied and focused tests passed.
5. The completed staging artifact was atomically published into the managed revision directory without downloading another copy. Exact final GGUF size was `5,680,522,464` bytes.
6. Retried the durable job; it completed successfully and appeared in the library. A stale durable error string remained in the historical job error field but did not affect completed state or validity.
7. Reconciled the library: `valid_models=3`, and the Q4_K_M artifact was visible with the expected repository, revision, group, path, file count, and total size.
8. Created disabled profile `qwen3.5-9b-q4-k-m`, validated it successfully, then enabled it.
9. Disabled and removed the old deleted-model profile `qwen3.5-9b` after its model file was confirmed missing; this was necessary to let the router start and did not delete any model file.
10. Started the managed CUDA router (`b11060`, `0.4.1-dev`), reached `ready`, loaded `qwen3.5-9b-q4-k-m`, observed native metadata including Q4_K Medium and 8.95B parameters, then unloaded it successfully.
11. Stopped the managed router. Durable state was `stopped`, no PID remained, managed port `1234` was released, and no orphaned managed server remained.
12. Stopped the single development backend/frontend pair after acceptance.

No access token was printed or stored in acceptance artifacts. The model is public; the configured token was not required for this transfer.

## Tests and validation

Focused downloader gate:

- `pytest --no-cov tests/test_download_worker.py`: **22 passed**.
- Ruff and mypy passed for the focused change.

Full backend gate:

- **287 passed, 1 skipped**.
- Branch coverage: **90.06%**, above the configured 85% threshold.
- Start `2026-09-26T09:24:59.7652269Z`; end `2026-09-26T09:25:42.7611305Z`; elapsed 43.00s.
- Ruff: passed.
- Strict mypy: passed for 50 source files.
- `git diff --check`: passed.

Frontend gate:

- **27 tests passed**.
- Production build passed.

Automated tests use mocks and temporary state; no live upstream service, secret, fixed port, installed runtime, or large download is required by the test suite. The live acceptance intentionally used the approved 5.29 GiB artifact only.

## Phase 5 gate assessment

- General reconciliation: complete from Phase 5.1.
- Offline/local behavior: complete from Phase 5.2.
- Approved live download acceptance: passed.
- Unmanaged files deleted: none. Only the user-requested obsolete profile record was removed; unmanaged model files were preserved.

## Next action

Begin Phase 6.1 — authenticated connection tests. Do not begin Phase 7 until the Phase 6 gate passes.

## Suggested commit message

`fix: make model download publication idempotent`
