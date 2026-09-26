# Windows one-folder packaging

The package contains the Python control plane and compiled static frontend assets. Keep llama.cpp runtimes and application data outside the package so updates never replace runtime binaries or user state.

From the repository root, with the project `.venv` configured:

```powershell
.\packaging\build-windows.ps1
```

The output is `dist\llamawebui\`. Copy registered llama.cpp runtimes beside the package or register their existing paths through the control plane. Set `LLAMAWEBUI_DATA_DIR` to an external data directory when launching the packaged executable so databases, backups, generated keys, downloads, and profiles survive package replacement.

Packaging is intentionally Windows-only in this slice. Linux and macOS CI validate source/build compatibility; the release package target is Windows x64.

Updates must replace only package output after the application is stopped. Never overwrite `data/`, registered runtime directories, model files, generated keys, or database backups. Runtime upgrades remain explicit registration/install operations and must not silently replace an in-use runtime.
