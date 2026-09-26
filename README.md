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

## Test and quality checks

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
