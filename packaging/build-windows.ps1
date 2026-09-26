[CmdletBinding()]
param(
    [string]$Output = "dist",
    [string]$PythonPath = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$spec = Join-Path $PSScriptRoot 'windows-package.spec'

if ($PythonPath) {
    $python = (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
} else {
    $venvPython = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $python = $venvPython
    } else {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw "Python environment not found: $venvPython, and python.exe is not on PATH"
        }
        $python = $pythonCommand.Source
    }
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
