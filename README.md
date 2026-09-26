# Llama Web UI

Local control plane and operator UI for an official `llama-server` process.

The application manages runtimes, model profiles, downloads, access keys, router lifecycle, and OpenAI-compatible connections. It does not replace or reimplement `llama-server` inference.

## Requirements

- Windows x64 development machine
- Python 3.12+
- Node.js and npm
- A registered or installed `llama-server` runtime
- A validated GGUF model and enabled model profile for inference

The repository's development environment uses `.venv` at the repository root. The backend dependency list is also available at `backend/requirements.txt` for machines that do not use the project metadata workflow.

## Set up a fresh Windows machine

Install the prerequisites first:

- Git
- Python 3.12 or newer, with **Add Python to PATH** enabled
- Node.js LTS, which includes npm
- The official `llama-server.exe` runtime build appropriate for the machine. CUDA builds also require a compatible NVIDIA driver and CUDA runtime support.

Clone the repository and create the Python virtual environment from the repository root:

```powershell
git clone https://github.com/NickDevoctomy/LlamaWebUI
Set-Location .\LlamaWebUI
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location .\backend
& ..\.venv\Scripts\python.exe -m pip install -e .
Set-Location ..
```

If the `py` launcher is unavailable, use `python -m venv .venv` instead. Do not select a different interpreter for backend commands: use `.venv\Scripts\python.exe` explicitly.

Install frontend dependencies:

```powershell
Set-Location .\frontend
npm install
Set-Location ..
```

The editable install is required because the backend uses a `src/` layout; installing only `requirements.txt` installs dependencies but does not install the local `llamawebui` package. The first application start creates the SQLite database and required data directories. No Python activation script or PowerShell execution-policy change is required when using the explicit interpreter path above.

## Start the backend

From the repository root, after completing the fresh-machine setup:

```powershell
Set-Location E:\Source\Misc\LlamaWebUI\backend
$env:LLAMAWEBUI_PORT = '18080'
$env:LLAMAWEBUI_DATA_DIR = '../data'
& ..\.venv\Scripts\python.exe -m llamawebui serve
```

If the editable install has not been completed, run this once from `backend` before starting the server:

```powershell
& ..\.venv\Scripts\python.exe -m pip install -e .
```

Leave this terminal running. Verify health from another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:18080/api/health
```

The backend control plane listens on port `18080` by default in the development setup.

## Start backend and frontend together

From the repository root, use the included PowerShell launcher. It starts both processes and stops them together with `Ctrl+C`:

```powershell
.\start.ps1
```

To bind both services to a specific address, pass an IPv4 address or `0.0.0.0`:

```powershell
.\start.ps1 192.168.1.50
.\start.ps1 0.0.0.0
```

The backend listens on port `18080` and the frontend on port `5173`. Use the machine's LAN address in a browser when connecting from another device. The control-plane login protects management endpoints, but credentials and session cookies must be transported over HTTPS whenever the control plane is reachable beyond loopback. Binding beyond loopback requires an API key for the managed router; do not expose either service to an untrusted network.

## Start the frontend

In another terminal:

```powershell
Set-Location E:\Source\Misc\LlamaWebUI\frontend
npm install
npm run dev -- --host 127.0.0.1
```

Open:

```text
http://127.0.0.1:5173/
```

The Vite development server proxies `/api` requests to the backend on port `18080`.

## Start the managed router

Use the runtime ID returned by `GET /api/runtimes`:

```powershell
$runtimes = Invoke-RestMethod http://127.0.0.1:18080/api/runtimes
$runtimes | Select-Object id, name, backend, build, usable
```

Start a selected runtime:

```powershell
$runtimeId = 'YOUR_RUNTIME_ID'
Invoke-RestMethod `
  http://127.0.0.1:18080/api/server/start `
  -Method Post `
  -ContentType 'application/json' `
  -Body (@{ runtime_id = $runtimeId } | ConvertTo-Json)
```

Check status:

```powershell
Invoke-RestMethod http://127.0.0.1:18080/api/server/status
```

Stop the router:

```powershell
Invoke-RestMethod http://127.0.0.1:18080/api/server/stop -Method Post
```

## Run a hello-world inference test

The native OpenAI-compatible endpoint is exposed by `llama-server` on port `1234` while the managed router is running.

Read a local generated key without printing it:

```powershell
$key = (Get-Content E:\Source\Misc\LlamaWebUI\data\generated\api-keys.txt -Raw).Trim().Split([Environment]::NewLine) | Select-Object -Last 1
```

Send a request:

```powershell
$body = @{
  model = 'qwen3.8-27b-cuda'
  messages = @(
    @{
      role = 'user'
      content = 'Reply with exactly: HELLO_WORLD'
    }
  )
  max_tokens = 64
  temperature = 0
  stream = $false
} | ConvertTo-Json -Depth 8

$response = Invoke-RestMethod `
  http://127.0.0.1:1234/v1/chat/completions `
  -Method Post `
  -Headers @{ Authorization = "Bearer $key" } `
  -ContentType 'application/json' `
  -Body $body

$response.choices[0].message.content
```

Expected output contains `HELLO_WORLD`.

Do not commit or print access tokens. The generated native key file is local machine state and is Git-ignored.

## Connect OpenCode

The control plane can generate an OpenCode-compatible configuration from the live router model list at:

```text
http://127.0.0.1:18080/api/integrations/opencode
```

The generated configuration uses this placeholder rather than embedding a raw token:

```text
{env:LLAMA_WEB_UI_API_KEY}
```

Set the environment variable in the terminal that launches OpenCode:

```powershell
$env:LLAMA_WEB_UI_API_KEY = 'YOUR_ACCESS_TOKEN'
opencode
```

For a remote client, use the router host address and the model alias returned by `/v1/models`, not a local GGUF filesystem path.

## Connect with curl or an OpenAI-compatible SDK

The managed router exposes the native OpenAI-compatible API directly. Keep the
access key in an environment variable and never place the raw value in a
script, configuration file, screenshot, or committed command history.

```powershell
$env:LLAMA_WEB_UI_API_KEY = 'YOUR_ACCESS_TOKEN'
$headers = @{ Authorization = "Bearer $env:LLAMA_WEB_UI_API_KEY" }

Invoke-RestMethod `
  http://127.0.0.1:1234/v1/models `
  -Headers $headers
```

For an OpenAI-compatible Python client, use the router's `/v1` base URL and a
placeholder environment lookup:

```python
import os
from openai import OpenAI

client = OpenAI(
   base_url="http://127.0.0.1:1234/v1",
   api_key=os.environ["LLAMA_WEB_UI_API_KEY"],
)
response = client.chat.completions.create(
   model="MODEL_ALIAS_FROM_V1_MODELS",
   messages=[{"role": "user", "content": "Reply with exactly HELLO_WORLD."}],
   max_tokens=64,
)
print(response.choices[0].message.content)
```

Replace only the model alias placeholder with an ID returned by `GET /v1/models`.
Do not use a local GGUF path as the API model name.

## Access-key lifecycle and restart behavior

1. Create an access key in the **Access** view and copy it once. The raw key is
  shown only at creation time.
2. Set `LLAMA_WEB_UI_API_KEY` in the process environment used by curl,
  OpenCode, or the SDK.
3. Start or restart the managed router after creating or revoking keys. The
  native `llama-server` process reads the generated key file at startup; it
  does not reload in-place key-file changes.
4. Revoke the key in **Access**, stop/restart the router as required, and verify
  clients receive HTTP 401. Never print the key while testing revocation.

The control plane is local-first and defaults to loopback. If either service is
bound beyond loopback, place it behind HTTPS and a trusted network boundary;
credentials and session cookies must not travel over plain HTTP. Do not expose
the native router or control plane to an untrusted network.

## Test and quality checks

## Build the Windows one-folder package

The release package target is Windows x64. Build it from the repository root:

```powershell
.\packaging\build-windows.ps1
```

This produces `dist\llamawebui\` with the Python control plane and compiled
static frontend. Keep `data/`, model files, generated keys, backups, and
registered llama.cpp runtimes outside the package directory. Set
`LLAMAWEBUI_DATA_DIR` to the external data directory when launching the
packaged executable so replacing the package does not replace user state.

Updates replace package files only after the application is stopped. Runtime
upgrades remain explicit registration/install operations; an application update
must never silently replace an installed or in-use llama.cpp runtime.

## Publish a GitHub release

Releases are tag-driven. Update `changelog.json` first, adding exactly one
entry whose `version` matches the tag without the leading `v`, then commit and
push the tag from `main`:

```powershell
git switch main
git pull --ff-only
# Edit changelog.json and set the release date and typed changes.
git add changelog.json
git commit -m "docs: prepare release v0.1.0"
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin main --follow-tags
```

Only when a version tag is pushed does the `Release` workflow start. It checks
that the tag points to a commit reachable from `main`, calls the same reusable
CI matrix used for ordinary branch and pull-request validation, and makes the
packaging job depend on that CI job. After CI succeeds it builds the Windows
one-folder package and creates
`llamawebui-v0.1.0-windows-x64.zip`, generates release notes from the matching
`changelog.json` entry, and publishes the GitHub release. The package contains
the application only; user data, models, access keys, backups, and registered
runtimes remain external.

The release workflow is intentionally not manually dispatchable: ordinary
branch pushes, including pushes to `main`, do not start a Release run. It runs
only for tags matching `vMAJOR.MINOR.PATCH`, requires the tag to be on `main`,
requires its CI dependency to pass, and requires exactly one matching changelog
version entry.

## Operator checklist

### First run

1. Register an existing `llama-server` runtime in **Runtimes**, or install a
   published build through the runtime installer.
2. Open **Discover**, search for a GGUF repository, inspect the revision,
   quantization, file count, and size, then explicitly select **Download**.
3. Confirm the completed artifact appears in **Models** and reconcile the
   library after importing files from an external managed directory.
4. Create or import a profile, select the runtime and model alias, validate it,
   and enable it only after validation succeeds.
5. Start the router from **Dashboard** or **Server**, wait for `Ready`, then
   use `/v1/models` to select the model alias for clients.
6. Create an access key in **Access** and configure clients through the
   environment-variable examples in this document.

### Routine operations

- Use **Server** to start, restart, stop, and inspect router logs and telemetry.
- Use **Models** to reconcile local files, inspect logical-model state, and
  remove only application-managed completed artifacts.
- Use **Profiles** to validate configuration after changing runtimes or model
  paths. A `Broken` profile indicates a missing or unavailable model and must
  be repaired or disabled before starting the router.
- Use **Access** to revoke keys. Stop/restart the router after key changes
  because the native key file is read at router startup.
- Use **Settings** and diagnostics export when collecting support information;
  never include raw keys, `.env` contents, or generated key-file contents.

### Safe update and recovery rules

- Stop the application before replacing the Windows package.
- Keep `data/`, backups, models, generated keys, and registered runtimes outside
  the package directory.
- Database backups are created under `data/backups/` before migrations and are
  retained to a bounded count. Do not delete the current database or backups
  during routine updates.
- Runtime updates are explicit and side-by-side. Do not overwrite an in-use
  runtime; register or install a new build, validate profiles, then switch
  deliberately.
- Do not start multiple copies of the application. Reuse the existing backend
  and frontend service pair, or stop the workspace-owned pair before restarting.

### Troubleshooting quick reference

| Symptom | Check | Safe action |
| --- | --- | --- |
| Login required or session expired | Control-plane session cookie | Sign in again; do not expose the cookie. |
| Router will not start | Runtime probe, enabled profiles, and profile validation state | Fix runtime/profile diagnostics, then retry. |
| Profile is `Broken` | Model path and completed download validity | Re-download the known source or update the profile path. |
| API returns 401 | Bearer key, router restart after key change, and model alias | Use an active key from the environment and restart after revocation/creation. |
| Download is paused or failed | Download job error and available disk space | Resume or retry; never manually publish partial files. |
| Hub is unavailable | Cached metadata and local Models state | Continue using known local models; retry discovery later. |
| UI has stale state | Browser session and control-plane health | Refresh the page and check `/api/health`. |

Support bundles and logs must be reviewed for secrets before sharing. Redaction
does not make it safe to publish raw environment files or native key files.

Run backend checks from `backend/`:

```powershell
Set-Location E:\Source\Misc\LlamaWebUI\backend
& ..\.venv\Scripts\python.exe -m pytest
& ..\.venv\Scripts\python.exe -m ruff check .
& ..\.venv\Scripts\python.exe -m mypy src/llamawebui
```

Run frontend checks from `frontend/`:

```powershell
Set-Location E:\Source\Misc\LlamaWebUI\frontend
npm test
npm run build
```

The backend test configuration enforces the configured 90% branch-coverage floor. A small number of live-stream and platform integration behaviors are documented separately as technical debt and are not unit-test substitutes for `llama-server`.

## Useful API endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Control-plane health |
| `GET /api/runtimes` | Registered runtimes |
| `GET /api/profiles` | Model profiles |
| `GET /api/library` | Validated downloaded models |
| `GET /api/server/status` | Router state, logs, timing, arguments, telemetry |
| `GET /api/server/models` | Native model list through the control plane |
| `GET /v1/models` | Native OpenAI-compatible model list |
| `POST /v1/chat/completions` | Native OpenAI-compatible chat completion |
| `GET /api/events` | Replayable control-plane events |

## Development notes

- Root `data/`, runtimes, generated keys, model files, caches, and build output are local machine state.
- Do not download the 93.7 GB development target during routine testing.
- Executables are launched with argument vectors, never shell command strings.
- Model downloads are revision-pinned and published atomically only after validation.
- For the current implementation handoff and remaining roadmap, see `docs/plans/active/sequential-progress.md` and `docs/plans/active/sequential-completion-plan.md`.
- Authoritative product requirements are in `docs/plans/llama-web-ui-plan.md`.
