# Headless WSL2 Build and Test

This guide documents the Linux validation setup used for the repository. It runs from Windows over SSH into an existing WSL2 Ubuntu instance; VS Code does not need to run inside WSL.

## Prerequisites

- WSL2 with Ubuntu and an SSH server listening on `localhost:2222`.
- A Linux user with permission to clone the public repository and install tools in their home directory.
- Git, `curl`, and `tar` in WSL.
- A Windows SSH client. Keep SSH private keys in the Windows user's `.ssh` directory; do not copy them into the repository or WSL checkout.

The example below assumes the WSL account is `nickp`, the SSH key file is `wsl_build_agent`, and the checkout is `~/LlamaWebUI`. Replace those values to match your machine. Never put private key contents or secrets in this document or in shell history.

## Connect and clone

Run these commands in Windows PowerShell. They connect directly to WSL using SSH and clone the public repository into the Linux home directory:

```powershell
$keyPath = Join-Path $HOME '.ssh\wsl_build_agent'
ssh -p 2222 -i $keyPath nickp@localhost 'git clone https://github.com/NickDevoctomy/LlamaWebUI.git ~/LlamaWebUI'
```

To test the current development branch rather than the repository's default branch:

```powershell
ssh -p 2222 -i $keyPath nickp@localhost 'cd ~/LlamaWebUI && git fetch origin alpha2 && git checkout -B alpha2 origin/alpha2'
```

Record the revision under test with:

```powershell
ssh -p 2222 -i $keyPath nickp@localhost 'cd ~/LlamaWebUI && git rev-parse --short HEAD && git status --short'
```

## Set up Python 3.12 and run backend checks

Install `uv` for the WSL user and let it install Python 3.12 and create the backend virtual environment. This avoids changing system Python packages or requiring administrator privileges:

```powershell
ssh -p 2222 -i $keyPath nickp@localhost 'curl -LsSf https://astral.sh/uv/install.sh | sh'
ssh -p 2222 -i $keyPath nickp@localhost 'export PATH="$HOME/.local/bin:$PATH"; cd ~/LlamaWebUI/backend && uv python install 3.12 && uv sync --extra dev --python 3.12'
```

Run the backend tests, Ruff, and mypy:

```powershell
ssh -p 2222 -i $keyPath nickp@localhost 'cd ~/LlamaWebUI/backend && .venv/bin/pytest'
ssh -p 2222 -i $keyPath nickp@localhost 'cd ~/LlamaWebUI/backend && .venv/bin/ruff check .'
ssh -p 2222 -i $keyPath nickp@localhost 'cd ~/LlamaWebUI/backend && .venv/bin/mypy src/llamawebui'
```

The test command applies the branch-coverage minimum configured in `backend/pyproject.toml`. `tests/test_router_process_tree.py` contains separate platform-specific integration tests: Windows verifies termination using Windows process tools, while Linux verifies that stopping the managed router also stops a child in its process group.

## Set up Node.js and run frontend checks

Install a Node.js 22 Linux binary in the WSL user's home directory (the example uses v22.17.0):

```powershell
ssh -p 2222 -i $keyPath nickp@localhost 'mkdir -p ~/.local/node-v22 && curl -fsSL https://nodejs.org/dist/v22.17.0/node-v22.17.0-linux-x64.tar.xz | tar -xJ --strip-components=1 -C ~/.local/node-v22'
```

Use `npm ci` to install exactly the dependencies in `frontend/package-lock.json`, then run the frontend tests and production build:

```powershell
ssh -p 2222 -i $keyPath nickp@localhost 'export PATH="$HOME/.local/node-v22/bin:$PATH"; cd ~/LlamaWebUI/frontend && npm ci && npm test && npm run build'
```

The Vite build writes generated files to `backend/src/llamawebui/static/`. Restore or remove only those generated build artifacts after validation if you want a clean checkout; do not discard other local changes.

## Security and state

- This guide contains no private keys, access tokens, Hugging Face credentials, or machine-specific secret contents.
- The SSH key is referenced by path only and remains on the Windows host.
- Keep the WSL checkout and its `.venv`, `node_modules`, and generated build files separate from committed source changes unless intentionally editing the project.
- Do not add local databases, model files, downloaded runtimes, generated API keys, or `.env` files to Git.
