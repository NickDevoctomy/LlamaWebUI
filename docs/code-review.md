# LlamaWebUI Code Review

## Scope and approach

This review covers the backend, frontend, persistence and process-management code, CI configuration, and automated tests at commit `06cf964` (`v0.3.1`). Findings are limited to issues that have a concrete correctness, security, operability, accessibility, or maintainability impact.

Severity is prioritized as follows:

- **P0 — Blocker:** a core workflow is unusable or a defect can directly expose protected functionality.
- **P1 — High:** a security, integrity, or reliability issue that should be addressed before wider deployment.
- **P2 — Medium:** a material operational, testing, or user-experience risk.
- **P3 — Low:** a correctness or maintainability issue with limited immediate impact.

## Findings

### CR-001 — Router authentication sends a redacted value

- **Severity:** P0 — Blocker
- **Location:** `backend/src/llamawebui/services/router_client.py:78-80`
- **Evidence:** `_headers()` checks whether a key exists, but sends the literal value `******` instead of the key. The test at `backend/tests/test_router_client.py:68-86` currently asserts this behavior.
- **Impact:** Any managed router configured with bearer authentication will reject model-list, load, unload, and event-stream requests. The control plane therefore cannot operate an authenticated router, and the test suite reinforces the defect rather than detecting it.
- **Action:** Send the actual key only in the in-memory request header, keep redaction confined to logs and diagnostics, and change the test to assert the expected bearer-header contract without printing the secret.
- **Done when:** Authenticated router requests succeed against a test server that checks the exact bearer value, while logs and error messages contain no key material.

### CR-002 — The default `admin/admin` account is not forced to change

- **Severity:** P1 — High
- **Location:** `backend/src/llamawebui/services/auth_service.py:29-31,118-138`; `backend/src/llamawebui/app.py:850-879`; `frontend/src/App.tsx:110-115`
- **Evidence:** First startup seeds a known password, and the UI advertises it as the default. The server exposes a `default_credentials` flag but does not enforce a password change before allowing management operations.
- **Impact:** A user who binds the control plane beyond loopback can leave the application permanently accessible with publicly documented credentials. A frontend warning is not an authorization control.
- **Action:** Replace the shared default with a first-run bootstrap flow using a one-time setup secret or generated password. Until the password is changed, allow only the session, password-change, and logout endpoints; do not render the default password in the shipped UI.
- **Done when:** Server-side tests prove that an unchanged bootstrap account cannot access runtime, download, token, or server-management endpoints, including when requests bypass the frontend.

### CR-003 — Native API keys remain in a long-lived plaintext file

- **Severity:** P1 — High
- **Location:** `backend/src/llamawebui/services/token_registry.py:30-34,108-132`; `backend/src/llamawebui/app.py:761-771`
- **Evidence:** Token hashes are stored in SQLite, but the raw tokens are persisted in `data/generated/api-keys.txt` so `llama-server` can read them on startup.
- **Impact:** Any local backup, accidental file disclosure, or process/user with read access to the generated file obtains every active inference credential. File permissions reduce exposure but do not provide secret-at-rest protection or per-key lifecycle isolation.
- **Action:** Track this as a deliberate security boundary and implement the planned lifecycle-only materialization: use OS credential storage where available, materialize the minimum required key file only while the router is starting/running, remove it on shutdown, and explicitly document platform fallbacks and residual risk.
- **Done when:** The application has a tested cleanup path for normal shutdown, startup failure, crash recovery, and token rotation, and diagnostics/backups never include raw keys.

### CR-004 — Managed model validation does not verify stored checksums

- **Severity:** P1 — High
- **Location:** `backend/src/llamawebui/services/model_artifact_registry.py:44-55`; checksum metadata is persisted by `backend/src/llamawebui/services/download_registry.py:117-123`
- **Evidence:** `is_valid()` accepts a completed artifact when each file exists with the expected byte count, but it does not compare the stored `sha256` value. The download worker does perform checksum verification during transfer.
- **Impact:** A model file modified after download but kept at the same size is reported as available and may be launched. This defeats the integrity guarantee advertised by the library and profile validation flows.
- **Action:** Centralize artifact validation so every availability, profile, reconciliation, and launch decision verifies the recorded digest when present. Preserve an explicit “unverified” state for legacy records without a digest rather than treating them as fully valid.
- **Done when:** Tests mutate a same-size artifact after download and verify that library listing, profile availability, and launch validation reject it.

### CR-005 — Unauthenticated health checks disclose filesystem locations

- **Severity:** P1 — High
- **Location:** `backend/src/llamawebui/app.py:687-693,841-848`
- **Evidence:** `/api/health` is public and returns the absolute data directory and database path.
- **Impact:** A remote caller can learn host-specific filesystem layout without authenticating. This information assists targeted attacks and is unnecessary for a liveness check.
- **Action:** Keep the unauthenticated response to a stable status/version payload. Move data paths and configuration details to an authenticated diagnostics endpoint, or expose only a coarse readiness state.
- **Done when:** Public health responses contain no host paths, while authenticated diagnostics retain the information needed for local troubleshooting.

### CR-006 — Login has no brute-force protection

- **Severity:** P2 — Medium
- **Location:** `backend/src/llamawebui/app.py:850-859`; no corresponding limiter is configured in `backend/src/llamawebui/config.py`
- **Evidence:** Login accepts unlimited attempts and immediately performs password verification; the HTTP middleware authenticates requests but does not throttle failed logins.
- **Impact:** The known default account and weak user passwords can be attacked at high volume if the control plane is accidentally reachable from a network.
- **Action:** Add bounded per-account and per-client-IP failure tracking with exponential backoff, a safe temporary lockout, and structured audit events. Make limits configurable and avoid revealing whether a username exists.
- **Done when:** Tests cover successful login, repeated failures, lockout expiry, concurrent attempts, and reset after a successful authenticated login.

### CR-007 — Token-file and database updates are not serialized

- **Severity:** P2 — Medium
- **Location:** `backend/src/llamawebui/services/token_registry.py:43-65,67-83`
- **Evidence:** Create and revoke read and rewrite the shared key file, then update SQLite, without an application lock or cross-store reconciliation protocol.
- **Impact:** Concurrent create/revoke requests can overwrite each other’s key-file changes. A database commit failure after a file write can also leave the database and native key file out of sync.
- **Action:** Serialize token mutations with a process-wide lock, use a durable mutation/reconciliation protocol for the two stores, and reconcile the key file from enabled database records during startup before launching the router.
- **Done when:** Concurrent mutation tests prove no enabled token is lost and simulated file/database failures converge to a documented consistent state.

### CR-008 — Background task failures can disappear without an operational signal

- **Severity:** P2 — Medium
- **Location:** `backend/src/llamawebui/services/download_coordinator.py:47`; `backend/src/llamawebui/services/router_supervisor.py:234-236`
- **Evidence:** Task completion callbacks do not inspect task exceptions, and the router watcher/log tasks have no explicit failure reporting at creation.
- **Impact:** An unexpected exception can stop progress publication or state supervision while the application continues to appear healthy. Operators may see a stalled download or stale router state with no useful log entry.
- **Action:** Add named task callbacks that retrieve and log exceptions, transition affected jobs/processes to a deterministic failure state, and avoid logging secrets or full command credentials.
- **Done when:** Tests inject failures into each background task and verify an error is logged plus the persisted state/event stream reflects the failure.

### CR-009 — Runtime installation buffers entire archives in memory

- **Severity:** P2 — Medium
- **Location:** `backend/src/llamawebui/services/llama_release_installer.py:135-145`
- **Evidence:** Each release asset is read with `response.content` before it is written to the staging directory. There is no preflight disk-space check based on the selected asset group.
- **Impact:** Large runtime archives can cause unnecessary memory pressure or process termination, and insufficient disk space is discovered only after a partial installation.
- **Action:** Stream responses directly to staged files, enforce bounded read/write behavior and an optional maximum size, and check aggregate asset size plus available space before downloading.
- **Done when:** Tests use a streaming response, verify bounded writes, reject an insufficient-space plan before transfer, and leave no promoted runtime after a failed download.

### CR-010 — Profile export and command actions fail silently

- **Severity:** P2 — Medium
- **Location:** `frontend/src/SetupPanels.tsx:321-332,421`
- **Evidence:** The UI invokes `exportProfile()` and `showCommand()` with `void`, but neither function catches request failures or exposes an error state.
- **Impact:** A server/network failure produces no feedback, leaving users unsure whether a file was exported or a command was generated.
- **Action:** Model both actions as mutations or add local error state, render an accessible error message, and keep the loading state on the initiating button.
- **Done when:** Component tests simulate failed export and command requests and verify a visible, associated error message and recovery path.

### CR-011 — Shared dialogs are not keyboard-accessible modal dialogs

- **Severity:** P2 — Medium
- **Location:** `frontend/src/SetupPanels.tsx:57-75`; destructive browser confirmations in `frontend/src/App.tsx:573,595`
- **Evidence:** The custom dialog declares `role="dialog"` but does not trap focus, handle Escape, or restore focus. User and role deletion use `window.confirm()` instead of the existing custom dialog component.
- **Impact:** Keyboard and assistive-technology users can lose focus behind a modal, and destructive actions have inconsistent, difficult-to-test interaction semantics.
- **Action:** Implement focus capture/trap, Escape handling, focus restoration, and an explicit pending-state close policy. Replace `window.confirm()` with the shared confirmation dialog and connect mutation errors to the relevant controls.
- **Done when:** Automated accessibility tests cover opening, tab order, Escape, focus restoration, cancellation, and destructive confirmation for both mouse and keyboard flows.

### DEFERRED !!! CR-012 — CI does not enforce frontend coverage or browser-level workflow tests

- **Severity:** P2 — Medium
- **Location:** `frontend/package.json:6-10`; `frontend/vitest.config.ts:4-10`; `.github/workflows/ci.yml:59-65`; backend coverage threshold `backend/pyproject.toml:38-50`
- **Evidence:** CI runs Vitest without coverage and has no browser/e2e job. Backend coverage is enforced at 85%, below the repository’s stated 90% target.
- **Impact:** Authenticated navigation, modal workflows, download controls, responsive layouts, and native-router integration can regress while unit tests and a build remain green.
- **Action:** Add focused frontend coverage thresholds and a small Playwright smoke suite for login, protected navigation, destructive confirmation, and the primary download/profile path. Align the backend threshold with the documented 90% branch target after measuring and closing gaps.
- **Done when:** CI fails below the agreed coverage thresholds and runs deterministic browser tests against mocked/local services without network or model downloads.

### CR-013 — Application version metadata is inconsistent

- **Severity:** P3 — Low
- **Location:** `backend/src/llamawebui/app.py:685`; canonical version files `VERSION`, `backend/pyproject.toml:5-9`, and `frontend/src/version.ts`
- **Evidence:** The FastAPI application is constructed with version `0.1.0` while the repository release is `0.3.1`.
- **Impact:** OpenAPI documents, health/debug output, and support reports can identify a different version from the packaged application, complicating incident triage and compatibility checks.
- **Action:** Define one release-version source and inject it into backend metadata, frontend display, packaging, and diagnostics during the build.
- **Done when:** A release-version consistency test compares all emitted version values with `VERSION`.

## Recommended implementation order

1. COMPLETE: Correct router authentication (CR-001) and enforce first-run credential rotation (CR-002).
2. COMPLETE: Reduce secret exposure and make token storage/lifecycle consistent (CR-003 and CR-007).
3. Restore artifact integrity guarantees and remove public path disclosure (CR-004 and CR-005).
4. Add login throttling and background-task failure observability (CR-006 and CR-008).
5. Fix runtime download resource handling and frontend error/accessibility behavior (CR-009 through CR-011).
6. Strengthen CI coverage/browser gates and unify version metadata (CR-013).

## DEFFERED

1. Playwright frontend test coverage (CR-012)

## Acceptance

1. Full test coverage passes, including ruff and mypy.
2. For every user-facing frontend change, perform a live UI acceptance test when the affected workflow requires it. Start the documented backend and frontend services, open the application in a browser, and exercise the changed workflow through the UI. Verify the resulting navigation, loading, success, error, disabled, confirmation, and destructive states as applicable. For authentication changes, test the real login/logout flow and any affected password or account-management flow rather than relying only on component tests.
3. Record live UI-test failures, diagnose them against the running application, and fix or explicitly report them before considering the review item complete.
4. Backend and frontend have been fully stopped after live acceptance, unless the user is expected to continue testing the running application.

## Notes

1. It is your responsibility to start the backend and/or frontend.
2. It is your responsibility to install any additional packages required for this development of this project. This does not include system utilities installed outside of this workspace.
3. local admin password for testing is 'password123', this server is not public facing.