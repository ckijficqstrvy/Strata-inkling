param(
    [ValidateSet('rdna2', 'rdna3', 'rx9070', 'strix')]
    [string]$HipVariant = 'rdna2',
    [string]$StrataRelease = 'v0.2.20',
    [string]$EngineVersion = '0.1.66',
    [switch]$SkipModelDownload
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

$dirs = @(
    (Join-Path $root 'downloads'),
    (Join-Path $root 'engine'),
    (Join-Path $root 'control'),
    (Join-Path $root 'models\Inkling-2-Mini-Strata'),
    (Join-Path $root 'packs\inkling-2-mini-sq-10\tokenizer'),
    (Join-Path $root 'logs')
)
foreach ($d in $dirs) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

$engineArchive = Join-Path $root "downloads\strata-inkling-$EngineVersion-windows-x86_64-hip-$HipVariant.tar.gz"
$controlArchive = Join-Path $root "downloads\strata-control-$EngineVersion-windows-x86_64.zip"

if (!(Test-Path (Join-Path $root 'engine\strata-inkling.exe'))) {
    Write-Host "[1/5] Downloading strata-inkling ($HipVariant)..."
    $url = "https://github.com/Niko1221/Strata/releases/download/$StrataRelease/strata-inkling-$EngineVersion-windows-x86_64-hip-$HipVariant.tar.gz"
    & curl.exe -fSL --retry 3 $url -o $engineArchive
    & tar.exe -xzf $engineArchive -C (Join-Path $root 'engine')
} else {
    Write-Host "[1/5] strata-inkling.exe already installed."
}

if (!(Test-Path (Join-Path $root 'control\python\python.exe'))) {
    Write-Host "[2/5] Downloading strata-control..."
    $url = "https://github.com/Niko1221/Strata/releases/download/$StrataRelease/strata-control-$EngineVersion-windows-x86_64.zip"
    & curl.exe -fSL --retry 3 $url -o $controlArchive
    & tar.exe -xf $controlArchive -C (Join-Path $root 'control')
} else {
    Write-Host "[2/5] strata-control already installed."
}

$modelDir = Join-Path $root 'models\Inkling-2-Mini-Strata'
$modelFile = Join-Path $modelDir 'Inkling-2-Mini-SQ-10.strata'
$expectedSha = 'ac4d452ad03e4930abd9d46f9c5eec22b2f75a1da45a1e49bd24ccd58b065934'

Write-Host "[3/5] Checking tokenizer & model files..."
foreach ($f in @('SHA256SUMS.txt', 'chat_template.jinja', 'config.json', 'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json')) {
    $dst = Join-Path $modelDir $f
    if (!(Test-Path $dst)) {
        & curl.exe -fSL --retry 3 "https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/$f" -o $dst
    }
}

if (-not $SkipModelDownload) {
    if (!(Test-Path $modelFile)) {
        Write-Host "Downloading Inkling-2-Mini-SQ-10.strata (10.47 GB)..."
        & curl.exe --http1.1 -fSL -C - --retry 5 "https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main/Inkling-2-Mini-SQ-10.strata" -o $modelFile
    }
    Write-Host "Verifying SHA-256 checksum..."
    $gotSha = (Get-FileHash -Algorithm SHA256 $modelFile).Hash.ToLower()
    if ($gotSha -ne $expectedSha) {
        throw "SHA-256 mismatch! Expected $expectedSha, got $gotSha"
    }
    Write-Host "SHA-256 verified OK!"
}

$python = Join-Path $root 'control\python\python.exe'
$pythonw = Join-Path $root 'control\python\pythonw.exe'

Write-Host "[4/5] Building tokenizer pack & icons..."
& $python -c "import sys; sys.path.insert(0, r'$root\control\tools'); import inkling_tokenizer; from pathlib import Path; inkling_tokenizer.convert_files(Path(r'$modelDir\tokenizer.json'), Path(r'$modelDir\chat_template.jinja'), Path(r'$root\packs\inkling-2-mini-sq-10\tokenizer'))"
& $python (Join-Path $root 'gui\make_icon.py')

Write-Host "[5/5] Writing tuned configs and desktop shortcut..."
$baseCfg = @{
    exe = (Join-Path $root 'engine\strata-inkling.exe')
    args = @('--native', $modelFile, '--max-context', '32768', '--kv', 'k8v8', '--draft', 'off')
    cwd = (Join-Path $root 'control')
    tokenizer = (Join-Path $root 'packs\inkling-2-mini-sq-10\tokenizer')
    model_name = 'inkling-2-mini-sq-10'
    aliases = @('inkling-2-mini', 'inkling', 'strata')
    arch = 'inkling2_loop'
    backend = 'hip'
    log = (Join-Path $root 'logs\strata-inkling-sq10.log')
    lib_dirs = @((Join-Path $root 'engine'))
    port = 8081
    drafting = 'off'
    parallel = 1
    sampling = @{ temperature = 1.0 }
    fit_max_tokens = $true
    open_browser = $true
}
$baseCfg | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $root 'strata-inkling-sq10.json') -Encoding utf8

$dsparkCfg = $baseCfg.Clone()
$dsparkCfg.args = @('--native', $modelFile, '--max-context', '32768', '--kv', 'k8v8', '--draft', 'dspark')
$dsparkCfg.log = (Join-Path $root 'logs\strata-inkling-sq10-dspark.log')
$dsparkCfg.drafting = 'dspark'
$dsparkCfg.sampling = @{ temperature = 0.2 }
$dsparkCfg | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $root 'strata-inkling-sq10-dspark.json') -Encoding utf8

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $root 'Inkling-2-Mini 控制台.lnk'))
$lnk.TargetPath = $pythonw
$lnk.Arguments = '"' + (Join-Path $root 'gui\app.py') + '" --launch'
$lnk.WorkingDirectory = $root
$lnk.IconLocation = (Join-Path $root 'gui\inkling.ico') + ',0'
$lnk.Description = 'Strata Inkling-2-Mini 圖形控制台（RX 6700 XT 調校版）'
$lnk.Save()

Write-Host "Setup complete! Double-click 'Inkling-2-Mini 控制台.lnk' to launch."
