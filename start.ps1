[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidatePattern('^(0\.0\.0\.0|(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d))$')]
    [string]$Ip = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'package.json') -PathType Leaf)) {
    throw "Frontend package.json not found: $frontend"
}

$env:PYTHONPATH = Join-Path $backend 'src'
$env:LLAMAWEBUI_HOST = $Ip
$env:LLAMAWEBUI_PORT = '18080'
$env:LLAMAWEBUI_ROUTER_HOST = $Ip
$env:LLAMAWEBUI_DATA_DIR = Join-Path $root 'data'

Write-Host "Starting backend on $Ip`:18080..."
$backendProcess = Start-Process -FilePath $python `
    -WorkingDirectory $backend `
    -ArgumentList @('-m', 'llamawebui', 'serve') `
    -PassThru

try {
    Write-Host "Starting frontend on $Ip`:5173..."
    $frontendProcess = Start-Process -FilePath 'npm.cmd' `
        -WorkingDirectory $frontend `
        -ArgumentList @('run', 'dev', '--', '--host', $Ip) `
        -PassThru

    Write-Host "Frontend: http://$Ip`:5173/"
    Write-Host "Backend:  http://$Ip`:18080/"
    Write-Host 'Press Ctrl+C to stop both processes.'

    while ($true) {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq [ConsoleKey]::C) {
                break
            }
        }
        if ($backendProcess.HasExited -or $frontendProcess.HasExited) {
            break
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    foreach ($process in @($frontendProcess, $backendProcess)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Write-Host "Stopping process $($process.Id)..."
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
