# Windows one-folder packaging

The package contains the Python control plane and compiled static frontend assets. Keep llama.cpp runtimes and application data outside the package so updates never replace runtime binaries or user state.

From the repository root, with the project `.venv` configured:

```powershell
.\packaging\build-windows.ps1
```

The output is `dist\llamawebui\`. Copy registered llama.cpp runtimes beside the package or register their existing paths through the control plane. Set `LLAMAWEBUI_DATA_DIR` to an external data directory when launching the packaged executable so databases, backups, generated keys, downloads, and profiles survive package replacement.

Packaging is intentionally Windows-only in this slice. Linux and macOS CI validate source/build compatibility; the release package target is Windows x64.

Updates must replace only package output after the application is stopped. Never overwrite `data/`, registered runtime directories, model files, generated keys, or database backups. Runtime upgrades remain explicit registration/install operations and must not silently replace an in-use runtime.

## Creating a release

The application version is maintained in one place: the root [`VERSION`](../VERSION) file. The frontend footer reads this file at build time. Release metadata in `backend/pyproject.toml`, `frontend/package.json`, and `frontend/package-lock.json` is synchronized automatically by the release script.

Before releasing:

1. Set the intended semantic version in `VERSION`, for example `0.4.0`.
2. Add exactly one matching entry to `changelog.json`.
3. Set that entry's `release_date` to today's date in `yyyy-MM-dd` format.
4. Include at least one change in the entry.
5. Review and commit the feature changes normally.

From the repository root, run a validation/build-only release:

```powershell
.\packaging\release.ps1
```

The script derives the tag as `v<version>`, validates the changelog entry and today's date, rejects an existing local or origin tag, synchronizes package metadata, runs the frontend tests/build and backend pytest/Ruff/mypy gates, generates `release-notes.md`, and builds the Windows package.

After reviewing the generated package and release notes, publish the release explicitly:

```powershell
.\packaging\release.ps1 -Publish
```

`-Publish` commits the synchronized metadata and generated release notes, creates an annotated Git tag, pushes the current commit and tag to `origin`, and creates the GitHub release using the generated notes. It requires `git` credentials and the GitHub CLI (`gh`) to already be authenticated. The script does not create or modify the changelog entry for you, and it stops before tagging if the entry is missing, duplicated, empty, or not dated today.

For an intentional local packaging retry when quality gates have already passed, use `-SkipQualityGates`; this still performs version/changelog/tag validation and package generation:

```powershell
.\packaging\release.ps1 -SkipQualityGates
```
