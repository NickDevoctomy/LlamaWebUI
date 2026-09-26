# Phase 7.4 — Packaging and update behavior

**Status:** Complete  
**Date:** 2026-09-26

## Implementation

- Added a Windows PyInstaller one-folder specification at `packaging/windows-package.spec`.
- Added `packaging/build-windows.ps1` to build the package with the project virtual environment.
- Added `packaging/README.md` documenting external data preservation and explicit runtime update policy.
- Added equivalent Windows package instructions to the root `README.md`.
- Added a Windows CI check that the package definition and build script are present.
- The package includes the Python control plane and compiled static frontend; application data, databases, backups, generated keys, models, and runtimes remain external and are not replaced by package updates.
- Runtime upgrades remain explicit registration/install actions and are never silently performed by application updates.

## Validation

- Packaging files and README guidance were validated locally.
- Frontend: **27 tests passed**; production build passed.
- Backend: **294 passed, 1 skipped**, 89.90% branch coverage.
- Ruff: passed.
- Strict mypy: passed for 51 source files.
- `git diff --check`: passed.
- GitHub Actions CI passed on all three configured platforms: Linux, Windows, and macOS. macOS packaging is not required; its CI job provides source/build compatibility coverage.

- PyInstaller 6.22.3 was installed into the project virtual environment and the Windows package was built locally with `packaging/build-windows.ps1`.
- Packaged CLI smoke test passed: `dist\\llamawebui\\llamawebui.exe --help`.
- Final package contains 312 files totaling 49,960,861 bytes, including `llamawebui/static/index.html` and `llamawebui/migrations/script.py.mako` under the PyInstaller `_internal` directory.

## Next action

Begin Phase 7.5 — operator documentation. Do not begin the final Phase 7 gate until the operator documentation slice passes.

## Suggested commit message

`build: add Windows one-folder packaging definition`
