# Slice 0001 — Phase 0.1 API-key reload semantics

**Status:** Complete
**Date:** 2026-09-21
**Suggested commit:** `test: record api key reload semantics`

## Result

The selected local llama.cpp runtime does not reload `--api-key-file` changes while running. The managed router must restart after token creation, revocation, or any generated key-file update.

## Evidence

- Runtime: llama.cpp `0.4.1-dev`, build `11053`, commit `1af554f8f`.
- Method: temporary local server with `--api-key-file` and `--no-models-autoload`; replace the key file while the process remains running; query `/v1/models`.
- Statuses: initial key `200`; wrong/missing key `401`; old key after replacement `200`; new key after replacement `401`.
- Cleanup: temporary server and key file removed; no model, upstream service, application secret, or large download used.

## Required product implication

The UI must communicate the restart requirement and must not report a newly created or revoked token as active for native inference until the managed router has restarted successfully.
