# Phase 2.2 - Real runtime rollback acceptance

**Status:** Complete  
**Date:** 2026-09-22  
**Suggested commit:** `test: record real runtime rollback acceptance`

## Implementation

- No implementation changes were required; the existing runtime registration, profile validation, start, restart, rollback, and restoration paths passed live acceptance.
- Acceptance used a new temporary data directory and alternate control-plane/router ports. The repository database, generated keys, runtime binaries, and model files were not changed.
- The two registered local executables were distinguished by their managed paths: `runtime/llama-b11053-cpu-x64/llama-server.exe` and `runtime/llama-b11070-cpu-x64/llama-server.exe`. Both probed as usable and reported build `0.4.1-dev`.

## Live acceptance

- Created isolated registrations and enabled profiles for both runtime IDs:
  - `d5a3c84f-e423-4779-b36e-f58ce46708d8` - b11053 directory.
  - `651ddfcd-d802-42b1-9285-c64ec09d2b2c` - b11070 directory.
- Both profiles validated against the existing local `Qwen3.8-27B-UD-IQ4_XS.gguf` artifact.
- Started the newer b11070 runtime successfully; it reached `ready` with PID `19376`.
- Restarted onto the older b11053 runtime successfully; it reached `ready` with PID `31692`.
- Invoked controlled rollback successfully; it returned to the newer b11070 runtime with PID `32840`.
- Durable runtime sequence was newer -> older -> newer.
- Stopped the router successfully; final state was `stopped`, with no managed router listener remaining on port `1235`.
- The temporary acceptance control plane was terminated after cleanup.

## Restoration evidence

- Focused command: `..\\.venv\\Scripts\\python.exe -m pytest tests/test_server_api.py -k rollback --no-cov`
- Result: `2 passed, 13 deselected`.
- The tests cover candidate launch failure with successful restoration of the current runtime, and restoration failure while preserving the candidate error semantics.
- Tests use temporary paths and fake processes; they do not require secrets, fixed ports, live upstream services, or large downloads.

## Validation

- Full backend pytest: `240 passed`, branch coverage `90.04%`; start `2026-09-22T08:48:34.6298632Z`, end `2026-09-22T08:48:55.2797259Z`, elapsed `20.650s`.
- Ruff: passed; elapsed `0.030s`.
- Strict mypy: passed for 48 source files; exact absolute-path invocation reproduced success after one timed probe used the wrong working-directory context.
- Frontend tests: `19 passed`; elapsed `3.268s`.
- Frontend production build: passed; elapsed `3.172s`.
- `git diff --check`: passed; line-ending warnings only.
- Final `git status --short --untracked-files=all`: clean.

## Manual acceptance

Manual acceptance - Phase 2.2: Real runtime rollback acceptance

Preconditions:
1. Use the two existing local CPU runtime executables and an existing validated local GGUF.
2. Use an isolated data directory and alternate control-plane/router ports.
3. Do not print tokens or inspect generated key contents.

Steps:
1. Register both local runtimes and confirm each reports usable with a build identifier.
2. Create and validate one enabled profile for each runtime using the existing local GGUF.
3. Start the newer runtime and confirm `ready`.
4. Restart onto the older runtime and confirm `ready`.
5. Invoke rollback and confirm `ready` on the newer runtime.
6. Inspect the durable run sequence and confirm newer -> older -> newer.

Expected results:
- Both runtimes and profiles are accepted without mutating saved profiles.
- Start, restart, and rollback reach `ready`.
- Rollback restores the previous runtime selection after the controlled switch.
- No secrets appear in acceptance output.

Safe cleanup:
1. Stop the router and confirm the final state is `stopped`.
2. Confirm the managed router port is no longer listening.
3. Terminate the isolated control plane. Leave the repository database and model files untouched.

## Next action

Begin Phase 3.1 - logical-model lifecycle completion. Do not begin Phase 4 until the Phase 3 gate is complete.