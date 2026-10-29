<#
.SYNOPSIS
    Checks whether Nsight Systems can record CPU samples on this machine, and in which mode.

.DESCRIPTION
    Run from an elevated (administrator) PowerShell. Profiles CPU-bound Python in each way
    LlamaWebUI could drive nsys, then counts the CPU samples and thread-activity events in
    each report:

      profile                 nsys profile, capturing until the program exits
      launch+start            nsys launch, then nsys start / stop
      profile+stop            nsys profile --session-new, ended early with nsys stop
      later, sample@profile   nsys profile --session-new --start-later with the sampling
                              options, then two nsys start / stop captures
      later, sample@start     the same, with the sampling options given to nsys start

    For the modes that use nsys stop it also reports whether the program kept running after
    the (first) stop. Results, reports and logs go to temp\cpu-sampling-check.
#>
[CmdletBinding()]
param(
    [string]$Nsys
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $root ".venv\Scripts\python.exe"
$out = Join-Path $root "temp\cpu-sampling-check"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Get-ChildItem $out -File | Where-Object { $_.Extension -in ".log", ".nsys-rep", ".sqlite", ".txt" } | Remove-Item

if (-not $Nsys) {
    $Nsys = Get-ChildItem "$env:ProgramFiles\NVIDIA Corporation\Nsight Systems *\target-windows-x64\nsys.exe" |
        Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
}
if (-not $Nsys) { throw "Nsight Systems not found" }

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
$elevated = $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "nsys:     $Nsys"
Write-Host "Elevated: $elevated"
if (-not $elevated) { Write-Warning "Not elevated: Windows will refuse CPU sampling. Run this from an administrator PowerShell." }
Write-Host "Takes about a minute. Python console windows will open and close."

# CPU-bound Python for a given number of seconds. It appends a line to its heartbeat file
# every second, so the check can tell whether it survived a capture stopping.
$busy = @'
import sys, time
end = time.time() + float(sys.argv[1])
beat = open(sys.argv[2], "a") if len(sys.argv) > 2 else None
next_beat = time.time()
while time.time() < end:
    sum(i * i for i in range(10000))
    if beat and time.time() >= next_beat:
        beat.write(f"{time.time():.0f}\n"); beat.flush(); next_beat += 1
'@
$busyFile = Join-Path $out "busy.py"
Set-Content -Path $busyFile -Value $busy -Encoding ascii

$sampling = @("--sample=process-tree", "--cpuctxsw=process-tree")

function Invoke-Nsys([string[]]$Arguments, [string]$Log) {
    $ErrorActionPreference = "Continue"
    & $Nsys @Arguments 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Append -Encoding utf8
    return $LASTEXITCODE
}

function Start-Nsys([string[]]$Arguments, [string]$Name) {
    $quoted = $Arguments | ForEach-Object { if ($_ -match "\s") { "`"$_`"" } else { $_ } }
    return Start-Process -FilePath $Nsys -ArgumentList $quoted -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $out "$Name.out.log") -RedirectStandardError (Join-Path $out "$Name.err.log")
}

function Get-Heartbeats([string]$File) {
    if (Test-Path $File) { return @(Get-Content $File).Count } else { return 0 }
}

$results = [System.Collections.Generic.List[object]]::new()
function Add-Result([string]$Mode, [string]$Report, [string]$Note = "") {
    $results.Add([pscustomobject]@{ Mode = $Mode; Report = $Report; Note = $Note })
}

# 1. profile: one-shot capture until the program exits.
Write-Host "1/5 profile"
$log = Join-Path $out "profile.log"
Invoke-Nsys (@("profile", "--trace=none") + $sampling + @("--force-overwrite=true", "--output=$out\profile", $python, $busyFile, "5")) $log | Out-Null
Add-Result "profile" "$out\profile.nsys-rep"

# 2. launch+start: how LlamaWebUI profiles the router today.
Write-Host "2/5 launch+start"
$log = Join-Path $out "launch.log"
$session = "check-launch-$PID"
$process = Start-Nsys @("launch", "--session=$session", "--trace=none", $python, $busyFile, "6") "launch"
Start-Sleep -Seconds 1
Invoke-Nsys (@("start", "--session=$session") + $sampling + @("--force-overwrite=true", "--output=$out\launch")) $log | Out-Null
Start-Sleep -Seconds 3
Invoke-Nsys @("stop", "--session=$session") $log | Out-Null
$process.WaitForExit(60000) | Out-Null
Add-Result "launch+start" "$out\launch.nsys-rep"

# 3. profile+stop: capture from launch, ended early by nsys stop.
Write-Host "3/5 profile+stop"
$log = Join-Path $out "profile-stop.log"
$session = "check-profile-$PID"
$beat = Join-Path $out "profile-stop.beat.txt"
$process = Start-Nsys (@("profile", "--session-new=$session", "--trace=none") + $sampling + @("--force-overwrite=true", "--output=$out\profile-stop", $python, $busyFile, "8", $beat)) "profile-stop"
Start-Sleep -Seconds 4
Invoke-Nsys @("stop", "--session=$session") $log | Out-Null
$before = Get-Heartbeats $beat
Start-Sleep -Seconds 2
$after = Get-Heartbeats $beat
$process.WaitForExit(60000) | Out-Null
Add-Result "profile+stop" "$out\profile-stop.nsys-rep" ($(if ($after -gt $before) { "program kept running after stop" } else { "program ended at stop" }))

# 4 and 5. profile --start-later, then two start / stop captures.
$step = 4
foreach ($variant in @("profile", "start")) {
    Write-Host "$step/5 later, sample@$variant"
    $step++
    $name = "later-sample-at-$variant"
    $log = Join-Path $out "$name.log"
    $session = "check-later-$variant-$PID"
    $beat = Join-Path $out "$name.beat.txt"
    $profileArgs = @("profile", "--session-new=$session", "--start-later=true", "--trace=none")
    if ($variant -eq "profile") { $profileArgs += $sampling }
    $process = Start-Nsys ($profileArgs + @("--force-overwrite=true", "--output=$out\$name-unused", $python, $busyFile, "16", $beat)) $name
    Start-Sleep -Seconds 2
    $survived = $false
    foreach ($capture in 1, 2) {
        $startArgs = @("start", "--session=$session")
        if ($variant -eq "start") { $startArgs += $sampling }
        Invoke-Nsys ($startArgs + @("--force-overwrite=true", "--output=$out\$name-$capture")) $log | Out-Null
        Start-Sleep -Seconds 2
        Invoke-Nsys @("stop", "--session=$session") $log | Out-Null
        if ($capture -eq 1) {
            $before = Get-Heartbeats $beat
            Start-Sleep -Seconds 2
            $survived = (Get-Heartbeats $beat) -gt $before
        }
    }
    $process.WaitForExit(60000) | Out-Null
    $note = if ($survived) { "program kept running after first stop" } else { "program ended at first stop" }
    # nsys writes the first capture to the --output given to nsys profile, not to nsys start.
    $first = if (Test-Path "$out\$name-1.nsys-rep") { "$out\$name-1.nsys-rep" } else { "$out\$name-unused.nsys-rep" }
    Add-Result "later, sample@$variant #1" $first $note
    Add-Result "later, sample@$variant #2" "$out\$name-2.nsys-rep"
}

# Count what each report holds.
$count = @'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
def n(table): return db.execute(f"select count(*) from {table}").fetchone()[0] if table in tables else 0
print(f"{n('COMPOSITE_EVENTS')} {n('SCHED_EVENTS')}")
'@
$countFile = Join-Path $out "count.py"
Set-Content -Path $countFile -Value $count -Encoding ascii

Write-Host ""
foreach ($result in $results) {
    if (-not (Test-Path $result.Report)) {
        Write-Host ("{0,-26} no report written           {1}" -f $result.Mode, $result.Note) -ForegroundColor Red
        continue
    }
    $sqlite = [IO.Path]::ChangeExtension($result.Report, ".sqlite")
    Invoke-Nsys @("export", "--type", "sqlite", "--force-overwrite", "true", "-o", $sqlite, $result.Report) (Join-Path $out "export.log") | Out-Null
    $samples, $threadEvents = (& $python -I $countFile $sqlite) -split " "
    $colour = if ([int]$samples -gt 0) { "Green" } else { "Red" }
    Write-Host ("{0,-26} CPU samples: {1,7}   thread events: {2,7}   {3}" -f $result.Mode, $samples, $threadEvents, $result.Note) -ForegroundColor $colour
}
Write-Host ""
Write-Host "nsys messages:"
Get-ChildItem $out -Filter *.log | ForEach-Object {
    $name = $_.Name
    Get-Content $_.FullName | Where-Object { $_ -match "(?i)^\s*(warning|error)|fail|privilege|etw" } |
        Sort-Object -Unique | ForEach-Object { Write-Host "  ${name}: $_" }
}
Write-Host "`nReports and logs: $out"
