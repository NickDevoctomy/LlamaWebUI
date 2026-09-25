# Phase 1.3 — Diagnostics bundle

**Status:** Complete  
**Date:** 2026-09-21  
**Suggested commit:** `feat: add redacted diagnostics bundle`

## Implementation summary

Added an explicit `POST /api/diagnostics/export` operation backed by `DiagnosticsExporter`.

Changed files:

- `backend/src/llamawebui/services/diagnostics.py`
- `backend/src/llamawebui/services/logging_utils.py`
- `backend/src/llamawebui/app.py`
- `backend/tests/test_diagnostics.py`

The exporter writes a versioned JSON bundle atomically to the application-managed `data/diagnostics/diagnostics-latest.json` path. It includes whitelisted application settings, hashed/path-minimized metadata, SQLite schema/table information, runtime metadata, profile validation summaries, bounded server/download state, bounded router logs, and the bounded chronological structured application-log tail.

Sensitive fields are excluded rather than serialized: token values and hashes, key-file contents, raw profile configuration/presets, launch arguments, download destinations/file metadata, and environment/.env contents. Text error/log fields receive the existing redaction rules. The bundle is bounded to 100 records/log lines per collection and temporary files are removed after atomic replacement.

Structured logging now retains a 200-record formatted application tail in memory for diagnostics while preserving the existing redacted stream output. No database, model, token, runtime, or frontend state was changed.

## Tests and validation

- Focused diagnostics/logging tests: **6 passed** when run together; focused coverage alone is below the repository threshold because it intentionally omits most modules.
- Full backend tests: **236 passed**; **90.08% branch coverage**, meeting the configured 90% threshold. Final timed full run: start `2026-09-21T16:38:53.3783492+01:00`, end `2026-09-21T16:39:13.2729178+01:00`, elapsed **19.89 seconds**.
- Ruff: passed.
- Strict mypy: passed.
- Frontend tests: **19 passed**.
- Frontend production build: passed.
- `git diff --check`: passed.
- Tests are deterministic and do not require live upstream services, secrets, fixed ports, installed llama-server processes, or model downloads.

## Manual acceptance

No browser or runtime acceptance was required. The explicit export API, atomic publication, schema metadata, bounded output, and representative bearer/Hugging Face/token-key exclusions are covered by deterministic tests.

## Next action

Begin Phase 1 slice 1.4 — static frontend packaging. Do not begin Phase 2 until all Phase 1 slices and the Phase 1 gate pass.
