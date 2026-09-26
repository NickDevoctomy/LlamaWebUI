# Phase 7.3 — CI and platform matrix

**Status:** Complete  
**Date:** 2026-09-26

## Implementation

- Updated `.github/workflows/ci.yml` to run the existing frontend/backend quality workflow on a matrix of `ubuntu-latest` and `windows-latest`.
- Kept macOS out of the matrix because the project does not support macOS builds, per the release scope.
- Preserved dependency caching, frontend tests/build, backend pytest/coverage, Ruff, and mypy steps on both operating systems.
- No live model downloads, runtime installation, tokens, or llama-server processes were added to CI.

## Validation

- Matrix definition checked for both `ubuntu-latest` and `windows-latest`; no macOS runner configured.
- Local frontend tests: **27 passed**.
- Local frontend production build: passed.
- Local backend tests: **294 passed, 1 skipped**.
- Branch coverage: **89.90%**, above the configured 85% threshold.
- Ruff: passed.
- Strict mypy: passed for 51 source files.
- `git diff --check`: passed.

The GitHub-hosted Linux and Windows jobs require a pushed workflow run for remote execution; local validation covered the same commands on the current Windows environment.

## Next action

Begin Phase 7.4 — packaging and update behavior. Do not begin Phase 7.5 until packaging/update behavior is accepted.

## Suggested commit message

`ci: add supported Windows and Linux test matrix`
