[CmdletBinding()]
param(
    [string]$Output = "dist"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$spec = Join-Path $PSScriptRoot 'windows-package.spec'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    & $python -m pip install pyinstaller
    & $python -m PyInstaller --noconfirm --clean --distpath $Output $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
