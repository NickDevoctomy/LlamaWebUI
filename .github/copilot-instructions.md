# LlamaWebUI Copilot Instructions

## Start Every Session

- Read `docs/plans/active/README.md` first
- Treat `docs/plans/llama-web-ui-plan.md` as the authoritative overall design and requirements document.
- Run `git status --short` and `git log -3 --oneline --decorate` before editing. Preserve user changes and never reset or overwrite work that is not yours.
- Proactively start stopped local development/control-plane services and registered managed runtimes when they are required for the current slice's acceptance. Do not ask the user for permission solely because an in-scope service is stopped. Existing validated models may be loaded/unloaded when explicitly required by the current acceptance; restore their original state and verify managed cleanup afterward. Ask first only for out-of-scope destructive actions, secret creation, or large downloads/transfers.

## Architecture Boundaries

- FastAPI is the local control plane. Native `llama-server` remains the inference endpoint; do not proxy or reimplement inference in Python.
- Invoke executables with argument vectors, never through a shell command string.
- Keep long-running operations durable and restart-safe. Persist state before starting work and reconcile interrupted work at startup.
- Keep filesystem publication atomic. Downloads stay in hidden staging directories until every expected file passes validation.
- Treat paths and remote metadata as untrusted. Resolve paths, enforce managed-root containment, reject traversal, pin Hugging Face revisions, and validate shard completeness and file sizes.
- Prefer existing registries, domain models, and API patterns over parallel abstractions.

## Local State And Secrets

- Root `data/`, `runtime/`, build output, caches, and local environments are machine state and must remain Git-ignored.
- Never print, log, commit, screenshot, or include access tokens, Hugging Face tokens, key material, or `.env` contents in responses or tests.
- SQLite stores access-token HMACs and metadata, not raw tokens. The generated `data/generated/api-keys.txt` currently contains plaintext because llama.cpp requires raw keys in `--api-key-file`; it is restricted and ignored but is known security debt. Do not claim it is encrypted or fully secure.
- The planned hardening is OS credential storage plus lifecycle-only key-file materialization. Do not implement a reversible local encryption scheme with its key beside the ciphertext and call that secure.
- Do not delete or reset the user's local database, generated keys, downloaded models, or registered runtimes.

## Backend Practices

- Use Python 3.12, typed domain objects, SQLAlchemy repositories, and FastAPI request/response validation.
- Keep HTTP error semantics explicit and redact upstream/private details.
- Add focused deterministic tests for new behavior. Tests must not require live network access, fixed ports, installed llama.cpp, or large model downloads.
- Maintain the configured 90% branch-coverage floor.
- After backend changes run from `backend/`:

```powershell
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m mypy src/llamawebui
```

## Frontend Practices

- Build a quiet, dense operator interface consistent with the existing DM Sans/IBM Plex Mono visual system.
- Use Lucide icons, compact controls, restrained square corners, clear state labels, and stable dimensions. Avoid marketing layouts, decorative cards, nested cards, gradients, and ornamental effects.
- Make workflows complete: loading, empty, success, error, disabled, confirmation, and destructive states must all be represented.
- Never start a model download automatically. Show repository, quantization, completeness, file count, and total size before an explicit Download action.
- Preserve manual-path workflows while making validated application-managed paths the easy default.
- Verify desktop and `390x844` mobile layouts. Require no horizontal overflow, no content hidden behind fixed navigation, and readable long repository/path names.
- After frontend changes run from `frontend/`:

```powershell
npm test
npm run build
```

- For every user-facing frontend change, perform visual browser acceptance after the tests/build. Start the documented backend and frontend services when needed, open the app in the browser, and inspect the changed workflow at desktop width and `390x844`. Verify actual geometry (no overlap, clipping, or overflow), colors/borders/icons against the surrounding design, visible labels, hover/focus/disabled states, and the resulting destination after interaction. A passing Vitest test or build is not sufficient for UI acceptance; use browser DOM inspection and screenshots when visual differences matter. Add a focused test for the interaction, but do not treat it as a substitute for live visual verification.

- If `npm test` fails, inspect and report every failure. Do not assume a baseline failure is acceptable, do not advance the sequential plan, and do not change unrelated backend tests to improve the status.

## Live Development

- Start the backend from `backend/` with the shared portable data directory and Vite proxy port:

```powershell
$env:LLAMAWEBUI_PORT='18080'
$env:LLAMAWEBUI_DATA_DIR='../data'
..\.venv\Scripts\python.exe -m llamawebui serve
```

- Start the frontend from `frontend/`:

```powershell
npm run dev -- --host 127.0.0.1
```

- Use `http://127.0.0.1:5173/` for browser acceptance and `http://127.0.0.1:18080/api/health` for backend health.
- When query polling makes Playwright's stability checks time out, verify geometry first and use a direct DOM click only for the affected control. Do not treat the automation timeout as an application failure without evidence.

## Model Acceptance

- The recommended first real acceptance artifact is `unsloth/Qwen3.5-9B-GGUF`, revision `3885219b6810b007914f3a7950a8d1b469d598a5`, group `Qwen3.5-9B-Q4_K_M`, 5.29 GiB.
- Do not create real download jobs or tokens during automated acceptance. Do not initiate large downloads/transfers or secret-creating actions without explicit user direction; continue verification from state the user intentionally creates.

## Definition Of Done

- Make the smallest complete change that resolves the current user-visible dependency.
- Validate the narrow behavior immediately after the first edit, then run the relevant full quality gates.
- Perform live browser acceptance for user-facing workflows without mutating valuable user state.
- Run `git diff --check` and inspect `git status --short --untracked-files=all` for generated files or secrets.
- Leave development services running when the user is expected to test the slice, otherwise stop / close them.
- Run all backend and frontend tests, along with ruff and mypy, everything should pass.
