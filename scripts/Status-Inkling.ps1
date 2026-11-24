$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$stateFile = Join-Path $root 'logs\server-process.json'

if (!(Test-Path $stateFile)) {
    Write-Host "Status: STOPPED"
    exit 0
}

$state = Get-Content $stateFile -Raw | ConvertFrom-Json
$proc = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
if (!$proc) {
    Write-Host "Status: STOPPED (stale PID $($state.pid))"
    exit 0
}

Write-Host "Status  : RUNNING (PID $($state.pid), mode=$($state.mode), port=$($state.port))"
Write-Host "Config  : $($state.config)"
Write-Host "Web UI  : http://127.0.0.1:$($state.port)"
Write-Host "OpenAI  : http://127.0.0.1:$($state.port)/v1"
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($state.port)/health" -TimeoutSec 3
    Write-Host "Health  : $($health | ConvertTo-Json -Compress)"
    $models = Invoke-RestMethod -Uri "http://127.0.0.1:$($state.port)/v1/models" -TimeoutSec 3
    $ids = ($models.data | ForEach-Object { $_.id }) -join ', '
    Write-Host "Models  : $ids"
} catch {
    Write-Host "Health  : Waiting / unreachable ($($_.Exception.Message))"
}
