# LlamaWebUI Sequential Completion Plan

**Status:** Active execution plan
**Created:** 2026-09-21
**Repository:** `alpha`
**Purpose:** Provide one strictly sequential roadmap from the current implementation state to the first complete release.

## How this plan is used

This document is the operational plan for the remaining work. The overall design and requirements document remains `../llama-web-ui-plan.md`. The current progress record is `sequential-progress.md` in this folder.

Execution rules:

1. Work on exactly one numbered phase at a time.
2. Work on exactly one numbered slice within that phase at a time.
3. Do not start a later phase until the current phase gate is explicitly marked complete.
4. If a defect is found in an earlier phase, pause the current phase, fix the earlier-phase defect, rerun its gate, then resume the current phase. This is a correction, not a new feature phase.
5. Every completed slice must update `sequential-progress.md` with implementation, tests, validation, and the next slice.
6. Every completed slice must include a suggested commit message.
7. No real large model downloads, secrets, live upstream services, or installed llama-server processes are used in unit tests.
8. Timed test runs must report start time, end time, elapsed time, test count, and coverage.

## Current verified baseline

The following work is already present in the repository and is not repeated as planned work:

- Core FastAPI control plane, SQLite/Alembic persistence, React/Vite UI, and event broker.
- Runtime registration, probing, release discovery/install, switching, rollback, and CUDA companion handling.
- GGUF discovery, shard/projector grouping, durable downloads, progress, pause/resume/cancel, ETag/checksum validation, staging, atomic publication, and artifact deletion.
- Router lifecycle supervision, readiness, restart/crash handling, native model operations, model-event relay, keepalives, telemetry, and bounded SSE tests.
- Logical-model persistence/reconciliation, profile relationships, missing-state retention, and guarded missing-record removal.
- Profile CRUD, clone, reset, validate, JSON import/export, readable command export, and non-executing command import.
- Token creation/revocation, restricted native key-file generation, OpenCode configuration generation, and live authenticated inference/tool-call acceptance evidence.
- Backend quality gate: 224 tests passed, 90.01% branch coverage, Ruff passed, and strict mypy passed.
- Frontend production build passed.

The baseline contains known stale statements in older planning documents. This plan is the sequential source for remaining implementation work. The historical commit sequence is intentionally not repeated here; this document is focused only on remaining work.

## Phase 0 — Feasibility completion

**Status:** Incomplete. This is the first phase to close in the sequential completion pass.

### 0.1 Confirm API-key reload semantics

**Status:** Complete — evidence is recorded in `notes/0001-phase-0-1-api-key-reload.md`.

- Determine whether the selected llama.cpp runtime reloads `--api-key-file` changes without a router restart.
- Use a controlled local runtime test; do not infer behavior from documentation alone.
- Record the result and required UI behavior.

**Acceptance:** Result documented with command/runtime build, observed status codes, and whether restart is required.

### 0.2 Compare one older runtime build

**Status:** Complete — evidence is recorded in `notes/0002-phase-0-2-older-runtime-comparison.md`

- Probe one older available llama.cpp build against the current capability parser.
- Record option/device/router-mode differences.
- Confirm unsupported options remain visible as compatibility diagnostics rather than corrupting profiles.

**Acceptance:** Comparison artifact recorded; no profile data is mutated by an incompatible probe.

### Phase 0 gate

- [x] API-key reload behavior recorded.
- [x] Older-build comparison recorded.
- [x] Focused tests and runtime acceptance evidence added to progress documentation.

## Phase 1 — Application foundation completion

**Status:** Complete — all slices and the Phase 1 gate pass. Start only after Phase 0 gate.

### 1.1 Single-instance locking

- Add an application-owned lock under the selected data directory.
- Acquire it before starting the control plane.
- Return a clear existing-instance error without deleting or stealing another lock.
- Release it on clean shutdown and tolerate stale-lock recovery only with verified process ownership.

**Acceptance:** Two startup attempts cannot run simultaneously; clean shutdown permits a later start; stale-lock behavior is deterministic and tested.

### 1.2 Redacted structured logging

- Add structured application logging with levels and timestamps.
- Redact bearer tokens, Hugging Face tokens, key-file contents, authorization headers, sensitive environment values, and private upstream details.
- Preserve useful router/download lifecycle context without logging secrets.

**Acceptance:** Unit tests prove representative secrets never appear in emitted log records; normal lifecycle logs remain useful.

### 1.3 Diagnostics bundle

- Add an explicit diagnostics export operation.
- Include sanitized application state, runtime metadata, profile validation summaries, recent chronological logs, and database/schema information.
- Exclude tokens, key contents, `.env` values, and raw secret-bearing command arguments.
- Write the bundle atomically under an application-managed diagnostics directory.

**Acceptance:** Bundle export is deterministic, redacted, bounded, and covered by tests.

### 1.4 Static frontend packaging

- Build the frontend into a package-controlled static directory.
- Serve it from the Python service when available.
- Keep development Vite behavior unchanged.
- Add a packaged-mode smoke test using a temporary static directory.

**Acceptance:** A copied application directory can serve the built UI without a Vite dev server.

### Phase 1 gate

- [x] Single-instance behavior complete.
- [x] Structured redacted logging complete.
- [x] Diagnostics bundle complete.
- [x] Static frontend serving complete.
- [x] Backend/frontend quality gates pass.
- [x] Phase 1 packaged-start smoke test passes.

## Phase 2 — Runtime manager completion

**Status:** Complete — all slices and the Phase 2 gate pass. Start Phase 3 only after this gate.

### 2.1 Runtime capability diagnostics

**Status:** Complete — evidence is recorded in `notes/0007-phase-2-1-runtime-capability-diagnostics.md`.

- Expose clear per-runtime probe errors, unsupported router capabilities, device information, and option compatibility.
- Ensure profile validation reports actionable diagnostics without overwriting saved profiles.

### 2.2 Real runtime rollback acceptance

**Status:** Complete — evidence is recorded in `notes/0008-phase-2-2-real-runtime-rollback.md`.

- Register two existing local runtimes.
- Validate a profile against both.
- Start with the newer runtime, perform controlled rollback, and verify restoration behavior on candidate failure.
- Record runtime/build identifiers without exposing secrets.

### Phase 2 gate

- [x] Capability diagnostics are visible and tested.
- [x] Two runtimes work side by side.
- [x] Rollback and restoration have measured acceptance evidence.

## Phase 3 — Local library and profiles completion

**Status:** Complete — slices 3.1–3.3 and the Phase 3 gate are accepted. Detailed evidence is in `notes/0009-phase-3-1-logical-model-lifecycle.md`, `notes/0010-phase-3-2-profile-round-trip.md`, and `notes/0011-phase-3-3-profile-gate-acceptance.md`.

### 3.1 Logical-model lifecycle completion

- Review durable logical-model operations for refresh, missing-record repair, unlinking, removal, and profile relationship consistency.
- Add any missing lifecycle operation without creating a parallel library abstraction.
- Ensure the Models workspace exposes valid, missing, linked, and removable states clearly.

### 3.2 Profile round-trip acceptance

- Import a representative Windows command.
- Export it back to a readable command and generated preset.
- Verify typed options, unknown advanced options, quoting, shard paths, and disabled import behavior survive the round trip.

### 3.3 Phase 3 gate evidence

- [x] Logical-model lifecycle behavior is covered.
- [x] Command import/export round-trip is accepted.
- [x] Profile data remains unchanged on validation/import errors.
- [x] Frontend desktop and mobile profile workflows are accepted. The user accepted the supplied screenshots and desktop workflow; responsive layout defects remain explicitly deferred in [`../../tech-debt/profile-panel-responsive-layout.md`](../../tech-debt/profile-panel-responsive-layout.md). This records acceptance of the current workflow with known UI debt, not a claim that mobile layout is clean.

### Phase 3 gate

- [x] All Phase 3 slices complete.
- [x] Round-trip acceptance evidence recorded.
- [x] No known stale Phase 3 statements remain in the progress document.

## Phase 4 — Server lifecycle completion

**Status:** Complete — slices 4.1–4.2 and the Phase 4 gate pass. Start Phase 5 only after this gate.

### 4.1 Real-runtime lifecycle acceptance

**Status:** Complete — evidence is recorded in `notes/0012-phase-4-1-real-runtime-lifecycle.md`.

- Using an existing small/validated model and registered runtime, verify start, readiness, authenticated model list, load, inference, unload, restart, and stop.
- Verify no orphaned workers or occupied managed ports remain.

### 4.2 Real-runtime model-event capture

**Status:** Complete — evidence is recorded in `notes/0013-phase-4-2-real-runtime-model-events.md`.

- Capture at least one native model-event frame using a bounded client.
- Record whether the selected runtime emits events for load/unload/status transitions.
- Keep the existing deterministic SSE tests as the routine gate.

### Phase 4 gate

- [x] All Phase 4 slices complete.
- [x] Repeated lifecycle acceptance passes.
- [x] Real native model-event frames are captured for load and unload transitions.
- [x] Logs and durable run history remain consistent after each operation.
- [x] Lifecycle and native model-event acceptance evidence recorded.
- [x] Focused deterministic SSE tests pass.

## Phase 5 — Hugging Face and downloads completion

**Status:** Complete — slices 5.1–5.3 and the Phase 5 gate pass. Start Phase 6 only after this gate.

### 5.1 General library reconciliation

- Reconcile completed downloads, discovered external GGUF sets, logical models, profiles, and router-visible model paths through one documented flow.
- Preserve missing identities and avoid deleting unmanaged files.
- Ensure duplicate provenance cannot create duplicate visible models or false Broken states.

### 5.2 Offline and upstream-failure behavior

- Add deterministic fake-Hub/GitHub tests for cached metadata, transient failures, rate limits, retry exhaustion, and offline local operation.
- Ensure already-known local models and profiles remain usable when upstream services are unavailable.

### 5.3 Primary download acceptance

- Use only the approved small acceptance model for routine live tests.
- Verify complete download, interruption, resume, checksum/size validation, publication, profile creation, and loadability.
- Do not use the 93.7 GB target for routine acceptance.

### Phase 5 gate

- [x] General reconciliation is complete.
- [x] Offline/local behavior is tested.
- [x] Approved live download acceptance passes.
- [x] No unmanaged files are deleted.

## Phase 6 — Tokens and client onboarding completion

**Status:** Implemented feature set; formal phase gate remains blocked by earlier phases and must be closed only after Phases 0–5 are gated.

### 6.1 Authenticated connection tests

- Add deterministic fake-router tests for authenticated `/v1/models`, non-streaming completion, streaming completion, invalid token rejection, and sanitized errors.
- Keep these tests independent of real models and fixed ports.

### 6.2 Live authenticated acceptance

- With an existing validated model/profile and registered runtime:
  - Create a token and use it for authenticated model listing.
  - Verify a random token receives 401.
  - Verify non-streaming visible output.
  - Verify streaming visible output and `[DONE]`.
  - Verify one parseable tool call.
  - Revoke the token, restart as required by runtime semantics, and verify rejection.
- Never print or store the raw token in acceptance artifacts.

### 6.3 Onboarding documentation

- Verify generated OpenCode/curl/OpenAI SDK configuration examples contain only placeholders, not raw secrets.
- Document restart requirements and local-only control-plane behavior.

### Phase 6 gate

- [ ] Valid token succeeds.
- [ ] Random token fails with 401.
- [ ] Non-streaming and streaming visible output succeed.
- [ ] Tool-call response is parseable.
- [ ] Revoked token fails after the confirmed restart/reload behavior.
- [ ] Onboarding documentation is updated.

## Phase 7 — Hardening and release

**Status:** Not started. Start only after Phase 6 gate.

### 7.1 Backup and restore

- Back up the SQLite database before migrations or destructive maintenance.
- Retain a bounded number of backups.
- Add explicit restore validation and tests using temporary databases.
- Never overwrite the current database without an atomic replacement and recovery path.

### 7.2 Accessibility and responsive hardening

- Run keyboard, focus, semantic-label, and screen-reader-oriented checks on all workflows.
- Recheck desktop and `390x844` mobile layouts.
- Confirm no horizontal overflow, hidden fixed-navigation content, or inaccessible destructive actions.

### 7.3 CI and platform matrix

- Add clean-checkout backend/frontend quality jobs.
- Add Windows x64 CPU/CUDA acceptance coverage where available.
- Add Linux/macOS smoke coverage only if those platforms are release targets.
- Keep real large downloads outside normal CI.

### 7.4 Packaging and update behavior

- Produce the one-folder Windows package with static frontend assets and runtime-registration support.
- Verify copied-folder startup, data-directory selection, lock behavior, migrations, and clean shutdown.
- Add update checks that never silently replace installed llama.cpp runtimes.

### 7.5 Operator documentation

- Write setup, runtime registration/install, model discovery/download, profile, token, OpenCode, diagnostics, backup/restore, and troubleshooting guides.
- Base troubleshooting entries on observed failure output.

### Phase 7 gate

- [ ] Package starts on a clean Windows machine.
- [ ] Operator can register/install a runtime, discover/download/register a model, create a profile, start the router, and connect from OpenCode.
- [ ] Backup/restore, accessibility, offline behavior, and diagnostics are accepted.
- [ ] CI/platform evidence is complete.
- [ ] No unreviewed secrets or generated machine state are included.

## Final release gate

Release is complete only when every phase gate above is checked and the following are true:

- All planned phases are marked complete in this document.
- The progress document names the next phase accurately or states that the plan is complete.
- Backend and frontend quality gates pass from a clean checkout.
- No required acceptance step depends on the 93.7 GB routine-download target.
- A suggested release commit/tag message is recorded.

**Current next action:** Begin Phase 6.1 — authenticated connection tests. Do not begin Phase 6.2 until the deterministic connection-test gate passes.
