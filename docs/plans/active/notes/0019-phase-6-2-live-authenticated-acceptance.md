# Phase 6.2 — Live authenticated acceptance

**Status:** Complete  
**Date:** 2026-09-26

## Live acceptance

Used one LlamaWebUI backend/frontend development pair at a time. No additional server pair was left running after acceptance.

- Created one temporary access key named `phase-6-2-temporary`; the raw key was used only in memory and was never written to notes, logs, screenshots, or command output.
- Started the existing registered CUDA runtime/router (`b11060`, `0.4.1-dev`) through the control plane and reached `ready`.
- Authenticated native `GET /v1/models` succeeded and returned 3 model IDs.
- Authenticated non-streaming `POST /v1/chat/completions` succeeded with HTTP 200 and visible `hello` content.
- Authenticated streaming chat completion succeeded with HTTP 200 and included the `[DONE]` sentinel. The bounded check did not require printing the response body.
- Authenticated tool-call completion succeeded with HTTP 200; the returned function name was `get_weather` and its JSON arguments were parseable.
- A random bearer token was rejected with HTTP 401. The upstream response was not copied into the acceptance artifact.
- Revoked the temporary access key while the router was stopped, verified the database/API record became disabled, then restarted the router. The revoked key was absent from the native key file and was not used again.
- Generated OpenCode configuration through `/api/integrations/opencode`; it used the placeholder `{env:LLAMA_WEB_UI_API_KEY}` and contained no raw secret.
- Stopped the managed router after acceptance. Final state was `stopped`, PID was null, and managed port 1234 was released. The Windows exit code `3221225786` matched the previously documented controlled console-stop behavior; no orphaned server remained.

## Scope and safety

- Existing local model/profile/runtime state was used; no model download or large transfer was initiated.
- The temporary key was intentionally created and revoked as required by the slice.
- No raw access key was recorded. Existing user-created access keys and model files were not modified.

## Validation

- Deterministic Phase 6.1 suite remains **4 passed** in `tests/test_authenticated_connections.py`.
- Full Phase 6.1 backend gate previously passed: **291 passed, 1 skipped**, 90.06% branch coverage, Ruff, and mypy.
- Phase 6.2 introduced no source changes after that gate; Ruff, mypy, and `git diff --check` were re-run and passed.
- Frontend test/build gate remains **27 tests passed** and production build passed from the Phase 6.1 gate.

## Next action

Begin Phase 6.3 — onboarding documentation. Do not begin Phase 7 until the Phase 6 gate passes.

## Suggested commit message

`test: record live authenticated API acceptance`
