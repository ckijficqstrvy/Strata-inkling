param(
    [int]$Port = 11435,
    [switch]$LanAccess
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root 'control\python\python.exe'
$gwScript = Join-Path $root 'gateway\gateway.py'

if (!(Test-Path $python)) {
    throw "Environment not installed yet. Run Install.cmd first."
}

$cfgPath = Join-Path $root 'gateway-config.json'
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
} else {
    $cfg = [PSCustomObject]@{
        enabled = $true; host = '127.0.0.1'; port = $Port; api_key = ''
        auto_wake = $true; idle_unload_min = 0; default_mode = 'fast'
        default_context = 32768; default_kv = 'k8v8'; backend_port = 8081
    }
}
$cfg.port = $Port
if ($LanAccess) { $cfg.host = '0.0.0.0' }
$cfg | ConvertTo-Json -Depth 3 | Set-Content $cfgPath -Encoding utf8

Write-Host "Starting Strata Inkling Open Agent Gateway (OpenAI / Ollama / LM Studio / Anthropic)..." -ForegroundColor Cyan
& $python -u $gwScript
