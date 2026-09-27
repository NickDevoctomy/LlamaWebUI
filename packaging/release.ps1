[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$Publish,
    [switch]$SkipQualityGates,
    [string]$Output = "dist"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$versionPath = Join-Path $root 'VERSION'
$changelogPath = Join-Path $root 'changelog.json'

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

$canonicalVersion = (Get-Content -LiteralPath $versionPath -Raw).Trim()
if ($Version -and $Version -ne $canonicalVersion) {
    throw "Requested version $Version does not match VERSION ($canonicalVersion)."
}
$Version = $canonicalVersion
if ($Version -notmatch '^(?:0|[1-9]\d*)\.\d+\.\d+$') {
    throw "VERSION must contain a semantic version such as 0.3.0. Found: $Version"
}

$today = Get-Date -Format 'yyyy-MM-dd'
$document = Get-Content -LiteralPath $changelogPath -Raw | ConvertFrom-Json
$entries = @($document.versions | Where-Object { $_.version -eq $Version })
if ($entries.Count -ne 1) { throw "Expected exactly one changelog entry for version $Version." }
$entry = $entries[0]
if ([string]$entry.release_date -ne $today) {
    throw "Changelog entry for $Version must have today's date ($today), found $($entry.release_date)."
}
if (@($entry.changes).Count -eq 0) { throw "Changelog entry for $Version must contain at least one change." }

$tag = "v$Version"
$existingTag = git tag --list $tag
if ($existingTag) { throw "Git tag $tag already exists locally." }
$remoteTag = git ls-remote --tags origin "refs/tags/$tag"
if ($remoteTag) { throw "Git tag $tag already exists on origin." }

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Python environment not found: $python" }
Invoke-Checked $python @('packaging/sync-version.py', '--version', $Version) $root

if (-not $SkipQualityGates) {
    Invoke-Checked 'npm.cmd' @('test') (Join-Path $root 'frontend')
    Invoke-Checked 'npm.cmd' @('run', 'build') (Join-Path $root 'frontend')
    Invoke-Checked $python @('-m', 'pytest') (Join-Path $root 'backend')
    Invoke-Checked $python @('-m', 'ruff', 'check', '.') (Join-Path $root 'backend')
    Invoke-Checked $python @('-m', 'mypy', 'src/llamawebui') (Join-Path $root 'backend')
}

Invoke-Checked $python @('packaging/generate-release-notes.py', '--tag', $tag, '--output', 'release-notes.md')
Invoke-Checked (Join-Path $root 'packaging/build-windows.ps1') @('-Output', $Output)

if ($Publish) {
    Invoke-Checked 'git' @('add', 'VERSION', 'release-notes.md', 'backend/pyproject.toml', 'frontend/package.json', 'frontend/package-lock.json', 'backend/src/llamawebui/static')
    Invoke-Checked 'git' @('commit', '-m', "release: $tag")
    Invoke-Checked 'git' @('tag', '-a', $tag, '-m', "LlamaWebUI $tag")
    Invoke-Checked 'git' @('push', 'origin', 'HEAD', $tag)
    Invoke-Checked 'gh' @('release', 'create', $tag, '--title', "LlamaWebUI $tag", '--notes-file', 'release-notes.md')
} else {
    Write-Output "Validated and built $tag. Re-run with -Publish to commit, tag, push, and create the GitHub release."
}
