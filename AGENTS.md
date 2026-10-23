# AGENTS.md

Instructions for AI coding agents working in this repository.

## Rules

1. **Never run git commands that change anything, unless the user explicitly asks for that exact action in their current message.** This includes `add`, `commit`, `amend`, `reset`, `checkout`/`switch`, `stash`, `rebase`, `merge`, `tag`, `push` and branch create/delete. It also covers anything that changes GitHub: `gh pr create/merge/close`, `gh api` writes, releases and repository settings.
   - Read-only commands are fine: `git status`, `git log`, `git diff`, `git show`, `git branch --list`, `gh pr view`.
   - A question about the release flow ("then we push and tag?") is a question, not an instruction.
   - Leave changes uncommitted in the working tree. The maintainer reviews, commits and pushes.
2. **No AI attribution.** Never add `Co-Authored-By`, "Generated with…" or similar lines to commits, PRs, tags or files.
3. **Pushing a `v*` tag publishes a release.** Never create or push tags.
4. If an action is blocked or fails, stop and report it. Don't substitute a different or more destructive action.

## Project

LlamaWebUI is a local control plane and operator UI for an official llama.cpp `llama-server` process. It manages runtimes, model downloads, model profiles, access keys, users/roles and the router lifecycle. It does **not** proxy or reimplement inference: clients call `llama-server`'s OpenAI-compatible API directly (port `1234` by default).

- `backend/` is a Python 3.12 FastAPI app (`src/` layout, package `llamawebui`) using SQLAlchemy, Alembic migrations and SQLite. Entry point: `python -m llamawebui serve`. Business logic lives in `backend/src/llamawebui/services/`, and tests are in `backend/tests/`.
- `frontend/` is a React 19 + TypeScript app built with Vite, TanStack Query and Lucide icons, and tested with Vitest and jsdom.
- `packaging/` holds the Windows one-folder PyInstaller build, version sync and release scripts.
- `docs/plans/llama-web-ui-plan.md` is the authoritative design. `docs/code-review.md` tracks review findings, and `docs/tech-debt/` records accepted debt.
- Machine state (`data/`, `runtime/`, `.venv/`, `dist/`, `build/`, `temp/`) is git-ignored. Never delete or reset the user's database, generated keys, downloaded models or registered runtimes. Never print or log tokens, keys or `.env` contents.
- Development targets Windows x64. Always use the repo-root interpreter `.venv\Scripts\python.exe` explicitly.

## Build

Setup (from the repository root):

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location backend; & ..\.venv\Scripts\python.exe -m pip install -e ".[dev]"; Set-Location ..
Set-Location frontend; npm install; Set-Location ..
```

Quality gates. All of them must pass before work is considered done:

```powershell
# from backend/
..\.venv\Scripts\python.exe -m pytest        # enforces the coverage floor in pyproject.toml (currently 85% branch)
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m mypy src/llamawebui
# from frontend/
npm test
npm run build
```

- `npm run build` writes the compiled frontend into `backend/src/llamawebui/static/`. That output is committed and served by the backend, so expect it in the diff after a frontend build.
- Run the services for live UI acceptance with `.\start.ps1` from the root (backend on `127.0.0.1:18080`, frontend on `127.0.0.1:5173`, which proxies `/api` to the backend). See the README to start them separately. Stop them afterwards unless the user will keep testing.
- The maintainer often has the app and a model running. Before a live test, check for a running backend, frontend, router or `llama-server`, and ask before stopping any of them. Don't stop, restart or load models in a session you didn't start.
- To test without touching the maintainer's data, run a second backend on another port (for example `18081`) with `LLAMAWEBUI_DATA_DIR` pointing at a scratch copy of `data\llamawebui.db`. Link `models` and `runtimes` into the scratch folder (directory junctions, removed afterwards with `rmdir`), and create a throwaway login in the copy only. The router still listens on `1234`, so it must not be running elsewhere.
- The `VERSION` file at the root is the single version source. `packaging/sync-version.py` propagates it to `backend/pyproject.toml`, `frontend/package.json`, `frontend/package-lock.json` and the backend version module.
- `.\packaging\build-windows.ps1` builds the Windows package into `dist\llamawebui\`.

## CI/CD

- `.github/workflows/ci.yml` runs on every branch push, on pull requests and as a reusable workflow. It uses an Ubuntu/Windows/macOS matrix with Python 3.12 and Node 22, and runs `npm ci`, `npm test`, `npm run build`, `pytest`, `ruff` and `mypy`. Windows also checks that the packaging files exist. There is no frontend coverage gate or browser e2e job yet (CR-012, deferred).
- `.github/workflows/release.yml` runs only when a `vMAJOR.MINOR.PATCH` tag is pushed. It verifies the tag is on `main`, runs CI, generates release notes from the matching `changelog.json` entry, builds and zips the Windows package, and creates the GitHub release.
- The release flow belongs to the maintainer: `packaging/release.ps1` validates and builds, and `-Publish` commits, tags and pushes. Agents may run `release.ps1` without `-Publish` only when asked, and must never run `-Publish`, create tags or push (see Rules).
