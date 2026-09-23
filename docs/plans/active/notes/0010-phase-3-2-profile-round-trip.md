# Phase 3.2 - Profile round-trip acceptance

**Status:** Complete  
**Date:** 2026-09-23  
**Suggested commit:** `test: accept Windows profile command round trip`

## Implementation

- `parse_command()` now recognizes the representative command's short aliases (`-m`, `-ngl`, `-c`, `-fa`, `-lzm`, `-ot`, `-ctk`, `-ctv`, `-b`, `-ub`), parses `--option=value`, and handles Windows CRT-style quoting without invoking a shell.
- `render_command()` emits Windows arguments using standard-library CRT quoting; POSIX output remains shell-quoted. The parser rejects an unterminated Windows quote and ignores `--models-preset` in full executable command lines because it is router-owned, not a per-profile option.
- Command export UI now reads the endpoint's `text/plain` body rather than trying to decode JSON. Command import alias enablement validates the alias entered in its own dialog.
- Added end-to-end import -> export -> import coverage with a complete three-shard Windows model path, typed options, unknown value and flag, exact generated preset checks, and disabled state. Invalid imports are confirmed not to mutate existing profiles.
- Deliberately did not add shell execution or alter router-owned settings. Import remains parse-only and does not execute the supplied command. Runtime capability validation remains authoritative for each imported profile option.

## Tests

- Focused backend: `..\.venv\Scripts\python.exe -m pytest tests/test_model_profile.py tests/test_profile_api.py --no-cov` — 36 passed.
- Parser tests cover CRT quote/backslash round trip (including tab, embedded quote, and trailing slash), representative short options, equals form, unterminated quotes, and router preset omission.
- API round-trip checks typed configuration, unknown advanced options, quoted shard path, generated preset, and that both imported profiles are disabled; invalid import leaves persisted profiles unchanged.
- Frontend command-import interaction verifies alias/runtime/disabled request body, disabled display, and text command export.
- All tests use temporary model shards, fake runtime probes, and mocked HTTP fetches. No live upstream services, secrets, fixed router ports, installed llama-server process, or large downloads are required.

## Validation

- Full backend: `..\.venv\Scripts\python.exe -m pytest` — 252 passed; branch coverage 90.22% against the 90% floor. Backend pytest elapsed 19.53s.
- Ruff: `..\.venv\Scripts\python.exe -m ruff check .` — passed.
- Strict mypy: `..\.venv\Scripts\python.exe -m mypy src/llamawebui` — passed for 48 source files.
- Frontend: `npm test -- --run` — 23 passed.
- Production build: `npm run build` — passed.
- `git diff --check` — passed; Git noted only a generated CSS line-ending conversion warning.
- Combined final quality gate start `2026-09-23T13:07:25.7270033Z`, end `2026-09-23T13:07:52.7240345Z`, elapsed 26.997s.

## Manual acceptance

- User reports the Profiles workflow is usable in desktop mode. Supplied desktop/mobile screenshots show layout issues: the desktop screenshot clips profile actions at its right edge, while the mobile screenshots show wrapped header buttons and profile action rows extending beyond the viewport. This is user-provided visual evidence, not an independently repeated browser acceptance; a live desktop/mobile round trip is not claimed.
- Mobile responsive cleanup is deferred as standalone technical debt in [`../../tech-debt/profile-panel-responsive-layout.md`](../../tech-debt/profile-panel-responsive-layout.md). The active sequential plan was not extended or reordered. Phase 3's mobile workflow acceptance remains outstanding, so the Phase 3 gate stays open.
- The existing isolated backend/Vite sessions were stopped after test work as requested. No service is intentionally left running.

## Next action

Begin Phase 3.3 - collect Phase 3 gate evidence, including desktop and mobile profile workflow acceptance. Do not begin Phase 4 until the Phase 3 gate passes.
