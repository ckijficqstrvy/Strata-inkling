param(
    [ValidateSet('auto', 'hip-rdna2', 'hip-rdna3', 'hip-rx9070', 'hip-strix', 'cuda-cuda12', 'cuda-cuda13')]
    [string]$EngineFlavor = 'auto',
    [ValidateSet('auto', 'SQ-10', 'SQ-15', 'SQ-20')]
    [string]$ModelPack = 'auto',
    [string]$StrataRelease = 'v0.2.20',
    [string]$EngineVersion = '0.1.66',
    [int]$BackendPort = 8081,
    [int]$GatewayPort = 11435,
    [switch]$WithVision,
    [switch]$LanAccess,
    [switch]$SkipModelDownload,
    [switch]$LaunchGui
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Strata Inkling-2-Mini One-Click Installer & Tuner" -ForegroundColor Cyan
Write-Host "  Workspace: $root" -ForegroundColor DarkCyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Auto-detect GPU & pick optimal Strata engine package + model pack
$gpuName = ""
try {
    $gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'Microsoft Basic|Virtual|Parsec|Indirect' }
    $preferred = $gpus | Where-Object { $_.Name -match 'Radeon RX|Radeon AI|NVIDIA|RTX|GTX' } | Select-Object -First 1
    if (-not $preferred) { $preferred = $gpus | Select-Object -First 1 }
    if ($preferred) { $gpuName = [string]$preferred.Name }
} catch {}

if ($EngineFlavor -eq 'auto') {
    if ($gpuName -match 'RX 6[0-9]{3}|6700|6800|6900|6950|6600') {
        $EngineFlavor = 'hip-rdna2'
    } elseif ($gpuName -match 'RX 7[0-9]{3}|7900|7800|7700|7600') {
        $EngineFlavor = 'hip-rdna3'
    } elseif ($gpuName -match 'RX 90[0-9]{2}|R9700') {
        $EngineFlavor = 'hip-rx9070'
    } elseif ($gpuName -match '8060S|Strix Halo|AI Max') {
        $EngineFlavor = 'hip-strix'
    } elseif ($gpuName -match 'NVIDIA|RTX|GTX|Tesla') {
        $EngineFlavor = 'cuda-cuda12'
    } else {
        $EngineFlavor = 'hip-rdna2'
    }
}

if ($ModelPack -eq 'auto') {
    if ($gpuName -match '5090|4090|3090|7900 XTX|R9700|PRO 6000|PRO 5000') {
        $ModelPack = 'SQ-20'
    } elseif ($gpuName -match '5080|5070 Ti|4080|4070 Ti SUPER|7900 XT|7800 XT|9070|6800|6900|6950') {
        $ModelPack = 'SQ-15'
    } else {
        $ModelPack = 'SQ-10'
    }
}

$packMeta = @{
    'SQ-10' = @{ file = 'Inkling-2-Mini-SQ-10.strata'; bytes = 10471526400; sha = 'ac4d452ad03e4930abd9d46f9c5eec22b2f75a1da45a1e49bd24ccd58b065934'; needGb = 13 }
    'SQ-15' = @{ file = 'Inkling-2-Mini-SQ-15.strata'; bytes = 14348316672; sha = '2ea32eed533afba257e2cbee7b073dc2c3da80fe55d1d2d794779b5498e30737'; needGb = 17 }
    'SQ-20' = @{ file = 'Inkling-2-Mini-SQ-20.strata'; bytes = 19346685952; sha = '0bbfd52344f0dd4779a7807b5f73554a812f24c035e6877b63cd0d499f7e1b97'; needGb = 22 }
}[$ModelPack]

$isRdna2 = ($EngineFlavor -eq 'hip-rdna2')
$backendName = if ($EngineFlavor.StartsWith('cuda')) { 'cuda' } else { 'hip' }
$defaultKv = if ($isRdna2) { 'k8v8' } else { 'fp8' }
$defaultDraft = if ($isRdna2) { 'off' } else { 'dspark' }

Write-Host "  Detected GPU   : $(if ($gpuName) { $gpuName } else { 'Unknown (using default)' })"
Write-Host "  Engine Package : strata-inkling-$EngineVersion-windows-x86_64-$EngineFlavor"
Write-Host "  Model Pack     : $ModelPack ($($packMeta.file), $([math]::Round($packMeta.bytes / 1e9, 2)) GB)"
Write-Host "  Optimal Tuning : --kv $defaultKv --draft $defaultDraft --max-context 32768"
Write-Host ""

# 2. Check free disk space & tools
$driveRoot = [System.IO.Path]::GetPathRoot($root)
$driveInfo = New-Object System.IO.DriveInfo($driveRoot)
$freeGb = [math]::Round($driveInfo.AvailableFreeSpace / 1GB, 1)
if (-not $SkipModelDownload -and !(Test-Path (Join-Path $root "models\Inkling-2-Mini-Strata\$($packMeta.file)")) -and $freeGb -lt $packMeta.needGb) {
    throw "Not enough free disk space on $driveRoot ($freeGb GB free; need at least $($packMeta.needGb) GB)."
}
foreach ($tool in @('curl.exe', 'tar.exe')) {
    if (!(Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Required Windows tool '$tool' not found in PATH."
    }
}

$dirs = @(
    (Join-Path $root 'downloads'),
    (Join-Path $root 'engine'),
    (Join-Path $root 'control'),
    (Join-Path $root 'models\Inkling-2-Mini-Strata'),
    (Join-Path $root 'packs\inkling-2-mini-sq-10\tokenizer'),
    (Join-Path $root 'logs')
)
foreach ($d in $dirs) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# 3. Download & extract strata-inkling engine
$engineExe = Join-Path $root 'engine\strata-inkling.exe'
$engineArchive = Join-Path $root "downloads\strata-inkling-$EngineVersion-windows-x86_64-$EngineFlavor.tar.gz"
if (!(Test-Path $engineExe)) {
    Write-Host "[1/6] Downloading strata-inkling ($EngineFlavor)..." -ForegroundColor Yellow
    $url = "https://github.com/Niko1221/Strata/releases/download/$StrataRelease/strata-inkling-$EngineVersion-windows-x86_64-$EngineFlavor.tar.gz"
    & curl.exe -fSL -C - --retry 5 --connect-timeout 30 $url -o $engineArchive
    if ($LASTEXITCODE -ne 0) { throw "Failed to download strata-inkling engine." }
    & tar.exe -xzf $engineArchive -C (Join-Path $root 'engine')
} else {
    Write-Host "[1/6] strata-inkling.exe already installed." -ForegroundColor Green
}

# 4. Download & extract strata-control (bundled Python 3.12 + serve/server.py)
$python = Join-Path $root 'control\python\python.exe'
$pythonw = Join-Path $root 'control\python\pythonw.exe'
$controlArchive = Join-Path $root "downloads\strata-control-$EngineVersion-windows-x86_64.zip"
if (!(Test-Path $python)) {
    Write-Host "[2/6] Downloading strata-control (with bundled Python 3.12)..." -ForegroundColor Yellow
    $url = "https://github.com/Niko1221/Strata/releases/download/$StrataRelease/strata-control-$EngineVersion-windows-x86_64.zip"
    & curl.exe -fSL -C - --retry 5 --connect-timeout 30 $url -o $controlArchive
    if ($LASTEXITCODE -ne 0) { throw "Failed to download strata-control." }
    & tar.exe -xf $controlArchive -C (Join-Path $root 'control')
} else {
    Write-Host "[2/6] strata-control already installed." -ForegroundColor Green
}

# 5. Download tokenizer + model pack + verify/self-repair SHA-256
$modelDir = Join-Path $root 'models\Inkling-2-Mini-Strata'
$modelFile = Join-Path $modelDir $packMeta.file
$modelUrl = "https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/$($packMeta.file)"

Write-Host "[3/6] Downloading tokenizer & config files..." -ForegroundColor Yellow
foreach ($f in @('SHA256SUMS.txt', 'chat_template.jinja', 'config.json', 'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json')) {
    $dst = Join-Path $modelDir $f
    if (!(Test-Path $dst)) {
        & curl.exe -fSL --retry 3 "https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/$f" -o $dst
    }
}

if (-not $SkipModelDownload) {
    $needDl = $true
    if ((Test-Path $modelFile) -and ((Get-Item $modelFile).Length -eq $packMeta.bytes)) {
        $needDl = $false
        Write-Host "[4/6] Model file already downloaded ($($packMeta.file)); verifying integrity..." -ForegroundColor Yellow
    } else {
        Write-Host "[4/6] Downloading $($packMeta.file) ($([math]::Round($packMeta.bytes / 1e9, 2)) GB, resumable)..." -ForegroundColor Yellow
        & curl.exe --http1.1 -fSL -C - --retry 5 --connect-timeout 30 $modelUrl -o $modelFile
        if ($LASTEXITCODE -ne 0) { throw "Model download interrupted; re-run Install.cmd to resume." }
    }
    & $python (Join-Path $root 'scripts\Verify-Download.py') $modelFile $modelUrl $packMeta.sha "$($packMeta.bytes)"
    if ($LASTEXITCODE -ne 0) { throw "Model SHA-256 verification/repair failed." }

    if ($WithVision) {
        $visFile = Join-Path $modelDir 'Inkling-2-Mini-Vision.strata'
        $visUrl = 'https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/Inkling-2-Mini-Vision.strata'
        if (!(Test-Path $visFile) -or ((Get-Item $visFile).Length -ne 636526592)) {
            Write-Host "Downloading optional Vision shard (0.64 GB)..." -ForegroundColor Yellow
            & curl.exe --http1.1 -fSL -C - --retry 5 $visUrl -o $visFile
        }
        & $python (Join-Path $root 'scripts\Verify-Download.py') $visFile $visUrl '6589eff47f9c20bc3a032faa3d449f10bf4f04a84cc6b2ee8a44d51d2b6fc677' '636526592'
    }
} else {
    Write-Host "[4/6] Skipped model download (-SkipModelDownload)." -ForegroundColor DarkGray
}

# 6. Build tokenizer pack, icons, tuned configs, gateway config, and zero-console shortcut
Write-Host "[5/6] Building tokenizer pack, icons & tuned configs..." -ForegroundColor Yellow
$tokOut = Join-Path $root 'packs\inkling-2-mini-sq-10\tokenizer'
& $python -c "import sys; sys.path.insert(0, r'$root\control\tools'); import inkling_tokenizer; from pathlib import Path; inkling_tokenizer.convert_files(Path(r'$modelDir\tokenizer.json'), Path(r'$modelDir\chat_template.jinja'), Path(r'$tokOut'))"
if (Test-Path (Join-Path $root 'gui\make_icon.py')) {
    & $python (Join-Path $root 'gui\make_icon.py')
}

$fastArgs = @('--native', $modelFile, '--max-context', '32768', '--kv', $defaultKv, '--draft', $defaultDraft)
$dsparkArgs = @('--native', $modelFile, '--max-context', '32768', '--kv', $defaultKv, '--draft', 'dspark')
if ($WithVision -and (Test-Path (Join-Path $modelDir 'Inkling-2-Mini-Vision.strata'))) {
    $visPath = Join-Path $modelDir 'Inkling-2-Mini-Vision.strata'
    $fastArgs += @('--vision', $visPath)
    $dsparkArgs += @('--vision', $visPath)
}

$baseCfg = @{
    exe = $engineExe
    args = $fastArgs
    cwd = (Join-Path $root 'control')
    tokenizer = $tokOut
    model_name = 'inkling-2-mini-sq-10'
    aliases = @('inkling-2-mini', 'inkling', 'strata')
    arch = 'inkling2_loop'
    backend = $backendName
    log = (Join-Path $root 'logs\strata-inkling-sq10.log')
    lib_dirs = @((Join-Path $root 'engine'))
    port = $BackendPort
    drafting = $defaultDraft
    parallel = 1
    sampling = @{ temperature = 1.0 }
    fit_max_tokens = $true
    open_browser = $true
}
$baseCfg | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $root 'strata-inkling-sq10.json') -Encoding utf8

$dsparkCfg = $baseCfg.Clone()
$dsparkCfg.args = $dsparkArgs
$dsparkCfg.log = (Join-Path $root 'logs\strata-inkling-sq10-dspark.log')
$dsparkCfg.drafting = 'dspark'
$dsparkCfg.sampling = @{ temperature = 0.2 }
$dsparkCfg | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $root 'strata-inkling-sq10-dspark.json') -Encoding utf8

$gwCfgPath = Join-Path $root 'gateway-config.json'
if (!(Test-Path $gwCfgPath)) {
    @{
        enabled = $true
        host = if ($LanAccess) { '0.0.0.0' } else { '127.0.0.1' }
        port = $GatewayPort
        api_key = ''
        auto_wake = $true
        idle_unload_min = 0
        default_mode = 'fast'
        default_context = 32768
        default_kv = $defaultKv
        backend_port = $BackendPort
    } | ConvertTo-Json -Depth 3 | Set-Content $gwCfgPath -Encoding utf8
}

$control = Join-Path $root 'control'
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

Write-Host "[6/6] Running GPU selfcheck & creating desktop GUI shortcut..." -ForegroundColor Yellow
if (Test-Path $modelFile) {
    & $engineExe --selfcheck --native $modelFile --kv $defaultKv
}

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $root 'Inkling-2-Mini 控制台.lnk'))
$lnk.TargetPath = $pythonw
$lnk.Arguments = '"' + (Join-Path $root 'gui\app.py') + '" --launch'
$lnk.WorkingDirectory = $root
$lnk.IconLocation = (Join-Path $root 'gui\inkling.ico') + ',0'
$lnk.Description = 'Strata Inkling-2-Mini 圖形控制台 & Open Agent Gateway'
$lnk.Save()

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  安裝與調校全部完成！" -ForegroundColor Green
Write-Host "  1. 雙擊「Inkling-2-Mini 控制台.lnk」即可開啟無黑框 GUI" -ForegroundColor White
Write-Host "  2. 開放 Agent 閘道（相容 OpenAI / Ollama / LM Studio / Anthropic）：" -ForegroundColor White
Write-Host "     - OpenAI / LM Studio : http://127.0.0.1:$GatewayPort/v1" -ForegroundColor Cyan
Write-Host "     - Ollama 相容端點    : http://127.0.0.1:$GatewayPort" -ForegroundColor Cyan
Write-Host "     - Anthropic API      : http://127.0.0.1:$GatewayPort/v1/messages" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Green

if ($LaunchGui) {
    Start-Process -FilePath $pythonw -ArgumentList ('"' + (Join-Path $root 'gui\app.py') + '" --launch') -WorkingDirectory $root
}
