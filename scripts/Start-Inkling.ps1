param(
    [ValidateSet('fast', 'dspark')]
    [string]$Mode = 'fast',
    [int]$Port = 8081,
    [switch]$OpenBrowser,
    [switch]$NoWait
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$control = Join-Path $root 'control'
$python = Join-Path $control 'python\python.exe'
$server = Join-Path $control 'serve\server.py'
$logsDir = Join-Path $root 'logs'
$stateFile = Join-Path $logsDir 'server-process.json'

if (!(Test-Path $logsDir)) { New-Item -ItemType Directory -Force -Path $logsDir | Out-Null }

$config = if ($Mode -eq 'dspark') {
    Join-Path $root 'strata-inkling-sq10-dspark.json'
} else {
    Join-Path $root 'strata-inkling-sq10.json'
}
if (!(Test-Path $config)) { throw "Config file not found: $config" }

if (Test-Path $stateFile) {
    $state = Get-Content $stateFile -Raw | ConvertFrom-Json
    $proc = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Strata Inkling is already running (PID $($state.pid), mode=$($state.mode), port=$($state.port))."
        Write-Host "Chat UI: http://127.0.0.1:$($state.port)"
        if ($OpenBrowser) { Start-Process "http://127.0.0.1:$($state.port)" }
        exit 0
    }
}

if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already occupied by another process. Stop it first or pass -Port <other>."
}

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
    $psOut = & ollama ps 2>$null
    if ($psOut -and ($psOut | Measure-Object).Count -gt 1) {
        Write-Warning "Ollama currently has a model loaded in memory; for 100% VRAM speed on RX 6700 XT, unload Ollama models first."
    }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $logsDir "server-$Mode-$stamp.out.log"
$stderr = Join-Path $logsDir "server-$Mode-$stamp.err.log"

$argList = @(
    '-u', $server,
    '--engine', 'strata',
    '--config', $config,
    '--port', "$Port",
    '--offline'
)
if ($OpenBrowser) { $argList += '--open' }

$patchScript = Join-Path $root 'scripts\patch_inkling_thinking.py'
if (Test-Path $patchScript) {
    try {
        & $python $patchScript --control-dir $control | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Inkling thinking patch exited with code $LASTEXITCODE"
        }
    } catch {
        Write-Warning "Inkling thinking patch skipped: $_"
    }
}

$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'

$proc = Start-Process -FilePath $python -ArgumentList $argList -WorkingDirectory $control `
    -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru

@{
    pid = $proc.Id
    mode = $Mode
    port = $Port
    config = $config
    stdout = $stdout
    stderr = $stderr
    started_utc = (Get-Date).ToUniversalTime().ToString('o')
} | ConvertTo-Json | Set-Content -Path $stateFile -Encoding utf8

Write-Host "Starting Strata Inkling-2-Mini SQ-10 (mode=$Mode, PID=$($proc.Id), port=$Port)..."

if (-not $NoWait) {
    $deadline = (Get-Date).AddSeconds(90)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            Write-Error "Server exited unexpectedly (code $($proc.ExitCode)). Check $stderr"
            exit 1
        }
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($resp.status -eq 'ok') {
                $ready = $true
                break
            }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    if ($ready) {
        Write-Host "READY! Inkling-2-Mini SQ-10 loaded in 100% VRAM on RX 6700 XT."
        Write-Host "  Web Chat UI : http://127.0.0.1:$Port"
        Write-Host "  OpenAI API  : http://127.0.0.1:$Port/v1  (model: inkling-2-mini-sq-10)"
        Write-Host "  Anthropic   : http://127.0.0.1:$Port/v1/messages"
        Write-Host "  Logs        : $stderr"
    } else {
        Write-Warning "Server is still starting after 90s. Check $stderr or run Status-Inkling.cmd."
    }
}
