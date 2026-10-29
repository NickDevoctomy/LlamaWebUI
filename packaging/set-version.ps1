[CmdletBinding()]
param(
    # The new version, for example 0.6.0.
    [Parameter(Mandatory)][string]$Version
)

# Sets the app version everywhere in one go: the root VERSION file, the backend and
# frontend version files generated from it, and the built frontend the backend serves.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory = $root) {
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
        }
    } finally {
        Pop-Location
    }
}

if ($Version -notmatch '^(?:0|[1-9]\d*)\.\d+\.\d+$') {
    throw "Not a semantic version such as 0.6.0: $Version"
}

[System.IO.File]::WriteAllText((Join-Path $root 'VERSION'), "$Version`r`n")
Invoke-Checked (Join-Path $root '.venv\Scripts\python.exe') @('packaging/sync-version.py', '--version', $Version)
Invoke-Checked 'npm.cmd' @('run', 'build') (Join-Path $root 'frontend')

Write-Host "`nVersion set to $Version. Changed files:" -ForegroundColor Cyan
Invoke-Checked 'git' @('status', '--short', '--', 'VERSION', 'backend/pyproject.toml', 'backend/src/llamawebui/version.py',
    'frontend/package.json', 'frontend/package-lock.json', 'frontend/src/version.ts', 'backend/src/llamawebui/static')
