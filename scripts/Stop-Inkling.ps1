$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$stateFile = Join-Path $root 'logs\server-process.json'

if (!(Test-Path $stateFile)) {
    Write-Host "Strata Inkling is not running (no state file)."
    exit 0
}

$state = Get-Content $stateFile -Raw | ConvertFrom-Json
$serverPid = [int]$state.pid

# Stop any child strata-inkling.exe processes owned by this server
$children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$serverPid" -ErrorAction SilentlyContinue
$proc = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
if ($proc) {
    Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped Strata server (PID $serverPid)."
}
foreach ($c in $children) {
    Stop-Process -Id $c.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped child engine process $($c.Name) (PID $($c.ProcessId))."
}

# Safety sweep for any orphaned strata-inkling.exe launched from this workspace
$orphans = Get-CimInstance Win32_Process -Filter "Name='strata-inkling.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -like "$root*" }
foreach ($o in $orphans) {
    Stop-Process -Id $o.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped engine process (PID $($o.ProcessId))."
}

Remove-Item -Force -ErrorAction SilentlyContinue $stateFile
Write-Host "VRAM and RAM released."
