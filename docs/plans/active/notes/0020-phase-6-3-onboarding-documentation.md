# Phase 6.3 — Onboarding documentation

**Status:** Complete  
**Date:** 2026-09-26

## Documentation changes

Updated `README.md` with:

- Curl model-listing example using `LLAMA_WEB_UI_API_KEY` as an environment placeholder.
- OpenAI-compatible Python SDK example using the `/v1` base URL and a model alias returned by `/v1/models`.
- Explicit warning not to use local GGUF paths as API model IDs.
- Access-key creation, one-time display, environment-variable, revocation, and restart guidance.
- Confirmed native `llama-server` reads the generated key file at startup and does not reload in-place changes.
- Loopback-first and HTTPS/network-boundary guidance for non-loopback deployments.

No raw token, key-file content, generated machine state, or model state was added.

## Validation

- Documentation assertions confirmed required placeholders `YOUR_ACCESS_TOKEN` and `MODEL_ALIAS_FROM_V1_MODELS` are present.
- Documentation assertion confirmed restart guidance is present.
- Secret-pattern scan found no raw `lwui_` token or Phase 6.2 acceptance token.
- Ruff: passed.
- Strict mypy: passed for 50 source files.
- `git diff --check`: passed.
- No frontend or backend runtime behavior changed; the Phase 6.1 full quality gate remains valid: 291 passed, 1 skipped, 90.06% branch coverage, frontend 27 tests and build passed.

## Next action

Close the Phase 6 gate and begin Phase 7.1 — backup and restore. The Phase 6 gate must record valid-token, invalid-token, streaming, tool-call, revocation, and onboarding evidence before Phase 7 work begins.

## Suggested commit message

`docs: complete authenticated client onboarding guidance`
