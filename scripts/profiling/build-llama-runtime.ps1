<#
.SYNOPSIS
    Builds llama.cpp from source with debug symbols (CUDA) and deploys it to data\runtimes\<Name>.

.DESCRIPTION
    Produces a profiler-friendly build: full optimisation plus PDBs for every exe/dll, and
    -lineinfo on the CUDA kernels so Nsight Compute can map kernels back to source lines.

    Deploys llama-server, llama-bench and their DLLs, plus the CUDA runtime DLLs, so the folder
    is self-contained. The first time, register the printed llama-server.exe path in the app
    (Runtimes > Register). After later rebuilds the path is unchanged, so just re-probe it.

    The llama.cpp source is a checkout of a release tag, kept in the git-ignored temp folder:
    git clone --depth 1 --branch b11815 https://github.com/ggml-org/llama.cpp temp\llama.cpp-b11815-cuda

.EXAMPLE
    .\scripts\profiling\build-llama-runtime.ps1
    .\scripts\profiling\build-llama-runtime.ps1 -Clean
    .\scripts\profiling\build-llama-runtime.ps1 -SourceDir .\temp\llama.cpp-b12000-cuda -CudaArchitectures "89;120"
#>
[CmdletBinding()]
param(
    # Paths default to the repository root, two folders up from this script.
    [string]$SourceDir = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "temp\llama.cpp-b11815-cuda"),
    # Folder name under data\runtimes. Defaults to "<tag>-cuda-debugsym".
    [string]$Name,
    [string]$DataDir = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "data"),
    # 89 = RTX 40xx. One architecture keeps the CUDA compile time down.
    [string]$CudaArchitectures = "89",
    [string[]]$Targets = @("llama-server", "llama-bench"),
    # Wipe the CMake build folder first (needed after changing CMake options).
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Marks deploy folders this script owns, so it never mirrors over a downloaded runtime.
$marker = ".llamawebui-source-build"

# Windows PowerShell 5.1 turns any native stderr output (compiler warnings included) into a
# terminating error under "Stop", so native commands are judged by exit code instead.
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

$SourceDir = (Resolve-Path $SourceDir).Path
if (-not (Test-Path (Join-Path $SourceDir "CMakeLists.txt"))) {
    throw "Not a llama.cpp source tree: $SourceDir"
}

$tag = (& git -C $SourceDir tag --points-at HEAD) | Select-Object -First 1
if (-not $tag) { $tag = (& git -C $SourceDir rev-parse --short HEAD) }
if (-not $Name) { $Name = "$tag-cuda-debugsym" }

$buildDir = Join-Path $SourceDir "build-debugsym"
$deployDir = Join-Path (Join-Path $DataDir "runtimes") $Name

# --- Toolchain: MSVC environment (also puts VS's bundled CMake and Ninja on PATH) ---
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw "No Visual Studio installation with the C++ x64 tools was found." }
if (-not $env:CUDA_PATH) { throw "CUDA_PATH is not set. Install the CUDA toolkit." }

Write-Host "Visual Studio: $vsPath"
Write-Host "CUDA:          $env:CUDA_PATH"
$vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
$vcEnv = cmd /c "`"$vcvars`" >nul 2>nul && set"
if ($LASTEXITCODE -ne 0) { throw "vcvars64.bat failed with exit code $LASTEXITCODE" }
$vcEnv | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
}

# --- Configure and build ---
if ($Clean -and (Test-Path $buildDir)) { Remove-Item -Recurse -Force $buildDir }

# MSVC's default RelWithDebInfo uses /Ob1, which limits inlining and skews profiles.
# Use release-level optimisation (/O2 /Ob2) and keep the symbols.
$cmakeArgs = @(
    "-S", $SourceDir, "-B", $buildDir, "-G", "Ninja",
    "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
    "-DCMAKE_C_FLAGS_RELWITHDEBINFO=/O2 /Ob2 /Zi /DNDEBUG",
    "-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=/O2 /Ob2 /Zi /DNDEBUG",
    "-DCMAKE_CUDA_FLAGS=-lineinfo",
    "-DCMAKE_CUDA_ARCHITECTURES=$CudaArchitectures",
    "-DGGML_CUDA=ON",
    "-DLLAMA_BUILD_TESTS=OFF",
    "-DLLAMA_BUILD_EXAMPLES=OFF"
)
# llama.cpp counts git commits for its build number, which is wrong in a shallow clone.
# Take it from the release tag instead so --version (and the app's probe) report e.g. b11815.
if ($tag -match "^b(\d+)$") { $cmakeArgs += "-DLLAMA_BUILD_NUMBER=$($Matches[1])" }
Write-Host "`n== Configuring ($tag) ==" -ForegroundColor Cyan
Invoke-Native "cmake" $cmakeArgs

Write-Host "`n== Building $($Targets -join ', ') ==" -ForegroundColor Cyan
$started = Get-Date
Invoke-Native "cmake" (@("--build", $buildDir, "--target") + $Targets)
Write-Host ("Build took {0:mm\:ss}" -f ((Get-Date) - $started))

# --- Deploy ---
$binDir = Join-Path $buildDir "bin"
if (-not (Test-Path (Join-Path $binDir "llama-server.exe"))) { throw "Build produced no llama-server.exe in $binDir" }

if (Test-Path $deployDir) {
    if (-not (Test-Path (Join-Path $deployDir $marker))) {
        throw "$deployDir exists but wasn't created by this script. Pick another -Name."
    }
    $running = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($deployDir, [StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        throw "Stop the router first: $($running.Name -join ', ') is running from $deployDir"
    }
} else {
    New-Item -ItemType Directory -Path $deployDir | Out-Null
}

Write-Host "`n== Deploying to $deployDir ==" -ForegroundColor Cyan
# /MIR keeps the folder an exact copy of the build output (drops stale files from older builds).
$robocopyArgs = @($binDir, $deployDir, "*.exe", "*.dll", "*.pdb", "/MIR", "/XF", $marker, "/NJH", "/NJS", "/NP", "/NDL")
& robocopy @robocopyArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

# The CUDA runtime DLLs, so the folder doesn't depend on the toolkit being on PATH.
$cudaBin = Join-Path $env:CUDA_PATH "bin\x64"
if (-not (Test-Path $cudaBin)) { $cudaBin = Join-Path $env:CUDA_PATH "bin" }
foreach ($pattern in @("cudart64_*.dll", "cublas64_*.dll", "cublasLt64_*.dll")) {
    Copy-Item (Join-Path $cudaBin $pattern) $deployDir
}

Set-Content -Path (Join-Path $deployDir $marker) -Encoding utf8 -Value @(
    "source=$SourceDir", "tag=$tag", "cuda_architectures=$CudaArchitectures", "built=$(Get-Date -Format o)"
)

$server = Join-Path $deployDir "llama-server.exe"
Write-Host "`nDone. Smoke test:" -ForegroundColor Green
Invoke-Native $server @("--version")
Write-Host "`nRuntime executable: $server" -ForegroundColor Green
Write-Host "First build: register that path in the app. Rebuilds: re-probe the existing runtime."
