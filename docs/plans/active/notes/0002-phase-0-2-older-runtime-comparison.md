# Phase 0.2 — Older runtime comparison

**Status:** Complete
**Date:** 2026-09-21
**Suggested commit:** `test: compare older runtime capabilities`

## Implementation summary

No application code was changed. The slice was limited to probing two available llama.cpp builds and verifying compatibility diagnostics/profile preservation. The workspace now contains two runtime builds. `llama-b11053-cpu-x64` was treated as the older build and `llama-b11070-cpu-x64` as the newer comparison build.

The installed build was probed through the existing CLI:

- Older runtime: `runtime/llama-b11053-cpu-x64/llama-server.exe`
- Older commit: `1af554f8f`
- Newer runtime: `runtime/llama-b11070-cpu-x64/llama-server.exe`
- Newer commit: `0c3626ec0`
- Both reported version: `0.4.1-dev`
- Both probe results: usable; help options parsed; `--list-devices` reported no device
- Capability difference: no added or removed long options
- Probe errors: none for either runtime

No profile data was changed.

## Existing compatibility evidence

The current implementation already keeps capability probing separate from profile persistence:

- `parse_help_output` produces immutable `RuntimeCapabilities`.
- `validate_profile` returns unsupported-option diagnostics such as `runtime does not support --ctx-size`.
- Profile validation does not write the saved profile.
- Existing profile API coverage verifies validation errors do not mutate the saved profile.

## Tests and validation

- Focused compatibility/profile tests: **40 passed** (`test_runtime_capabilities.py`, `test_runtime_probe.py`, `test_model_profile.py`, `test_profile_api.py`; 1.84s; no coverage threshold applied to the focused subset).
- Full backend tests: **224 passed**, **90.01% branch coverage** (configured 90% threshold), **19.33s**; start `2026-09-21T14:45:06.1274285+01:00`, end `2026-09-21T14:45:25.4588460+01:00`.
- Ruff: passed.
- Mypy: passed.
- Frontend build: passed.
- Frontend tests: **19 passed**; production build passed.
- `git diff --check`: passed.
- No live upstream service, secret, fixed port, llama-server process, or model download was used for tests.

## Manual/runtime acceptance

Both runtime probes were run directly and returned usable capabilities with no probe errors. The older/newer option sets were identical and both reported no available device. Profile compatibility diagnostics and non-mutation behavior were covered by the focused tests.

## Next action

Phase 0 is complete. Begin Phase 1 slice 1.1 — single-instance locking.