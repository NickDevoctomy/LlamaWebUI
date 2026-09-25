# Phase 2.1 — Runtime capability diagnostics

**Status:** Complete
**Date:** 2026-09-21
**Suggested commit:** `feat: expose runtime capability diagnostics`

## Implementation

- Added reusable router capability classification to `RuntimeCapabilities`, including the required `models-preset` option and missing-router-option reporting.
- Changed runtime probe usability so version/help failures remain blocking, while device enumeration failures are reported as `unavailable` diagnostics without making an otherwise valid runtime unusable. Empty successful device output is represented as `none`.
- Expanded runtime API payloads with commit, probe errors, device status, router compatibility, missing router options, diagnostics, and help hash.
- Reused runtime diagnostics for router start, rollback, and profile validation. Device-only warnings do not block those operations; version/help failures and missing router support remain actionable blockers. Validation remains non-mutating.
- Added frontend runtime diagnostics display, device/router status details, commit identity, and a re-probe action with loading/error handling.
- Updated generated packaged frontend assets as required by the production build. No runtime binaries, model files, tokens, key files, or database state were changed.

## Tests

- Added parser coverage for router compatibility classification.
- Added probe coverage for successful device absence, device-only probe failure, and blocking version failure.
- Existing server API tests continue to cover unsupported router start behavior and profile validation behavior.
- Focused validation: `29 passed` across runtime capability, runtime probe, and server API tests.
- Tests use temporary paths and fake command runners; they do not require live upstream services, secrets, fixed ports, installed llama-server processes, or large downloads.

## Validation

- Full backend: `240 passed`, branch coverage `90.04%` (configured floor `90%`), elapsed `19.5697207s`; start `2026-09-21T18:43:41.5372507+01:00`, end `2026-09-21T18:44:01.1069714+01:00`.
- Ruff: passed.
- Strict mypy: passed for 48 source files.
- Frontend: `19 passed`; production `npm run build` passed.
- `git diff --check`: passed.
- Final focused regression/quality check: `23 passed`, Ruff passed, mypy passed, and diff check passed.

## Manual acceptance

Not required for this backend/API and deterministic frontend diagnostics slice. The frontend build verifies the packaged UI compiles; no runtime or browser state mutation is needed.

## Next action

Begin Phase 2.2 — real runtime rollback acceptance. Do not begin Phase 3 until the Phase 2 gate is complete.