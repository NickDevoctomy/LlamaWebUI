[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Tag,
    [string]$Output = "release-notes.md"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$changelogPath = Join-Path $root 'changelog.json'
$document = Get-Content -LiteralPath $changelogPath -Raw | ConvertFrom-Json
$version = $Tag -replace '^v', ''
$entry = @($document.versions) | Where-Object { $_.version -eq $version } | Select-Object -First 1
if ($null -eq $entry) {
    throw "No changelog entry found for tag $Tag (expected version $version)."
}

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("# LlamaWebUI $Tag")
$lines.Add("")
$lines.Add("Released: $($entry.release_date)")
$lines.Add("")
$groups = @($entry.changes | Group-Object type)
foreach ($group in $groups) {
    $lines.Add("## $($group.Name)")
    $lines.Add("")
    foreach ($change in $group.Group) {
        $lines.Add("- $($change.description)")
    }
    $lines.Add("")
}

Set-Content -LiteralPath $Output -Value ($lines -join [Environment]::NewLine) -Encoding utf8NoBOM
Write-Output (Resolve-Path $Output)
