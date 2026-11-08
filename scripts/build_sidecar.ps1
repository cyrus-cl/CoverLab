param([switch]$Clean)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $repo 'services\engine\.dist'
$target = Join-Path $repo 'apps\desktop\src-tauri\resources\engine'
$work = Join-Path $repo '.runtime\pyinstaller'

function Assert-ChildPath([string]$Candidate) {
    $root = [IO.Path]::GetFullPath($repo).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath($Candidate)
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe generated path: $full"
    }
}

Assert-ChildPath $dist
Assert-ChildPath $target
Assert-ChildPath $work

if ($Clean) {
    if (Test-Path -LiteralPath $dist) {
        Remove-Item -LiteralPath $dist -Recurse -Force
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    if (Test-Path -LiteralPath $work) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
New-Item -ItemType Directory -Force -Path $work | Out-Null

uv run --all-packages --locked --python 3.12 python (Join-Path $repo 'scripts\prepare_media_runtime.py')
if ($LASTEXITCODE -ne 0) {
    throw "Pinned FFmpeg runtime preparation failed with exit code $LASTEXITCODE"
}

uv run --all-packages --locked --python 3.12 pyinstaller --noconfirm --clean `
    --distpath $dist `
    --workpath $work `
    (Join-Path $repo 'services\engine\ai4music-engine.spec')
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$sidecarDist = Join-Path $dist 'ai4music-engine'
if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Path (Join-Path $sidecarDist '*') -Destination $target -Recurse -Force

$executable = Join-Path $target 'ai4music-engine.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    throw 'Sidecar executable missing'
}
if (-not (Test-Path -LiteralPath (Join-Path $target '_internal'))) {
    throw 'Sidecar runtime directory missing'
}

foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $target "_internal\bin\$tool"))) {
        throw "Bundled media tool missing: $tool"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $target '_internal\torch\lib\torch_cpu.dll'))) {
    throw 'Bundled CPU inference runtime missing'
}
foreach ($registryFile in @('demucs-htdemucs.json', 'LICENSE-demucs.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $target "_internal\model-registry\$registryFile"))) {
        throw "Bundled separation model provenance missing: $registryFile"
    }
}
foreach ($module in @('ctranslate2','faster_whisper','av','av.libs','onnxruntime','transformers','tokenizers','safetensors')) {
    if (Test-Path -LiteralPath (Join-Path $target "_internal\$module")) {
        throw "Unnecessary local ASR dependency in cloud installer: $module"
    }
}

$runtimeOutput = & $executable --runtime-check
if ($LASTEXITCODE -ne 0) {
    throw "Frozen inference runtime check failed with exit code $LASTEXITCODE"
}
$runtime = $runtimeOutput | ConvertFrom-Json
if ($runtime.event -ne 'engine_runtime' -or $runtime.torch_version -ne '2.5.1+cpu') {
    throw 'Frozen sidecar must contain the pinned CPU inference runtime'
}
$expectedVersions = @{
    scipy_version = '1.18.1'
}
foreach ($field in $expectedVersions.Keys) {
    if ($runtime.$field -ne $expectedVersions[$field]) {
        throw "Frozen runtime version mismatch: $field"
    }
}
foreach ($tool in @('ffmpeg', 'ffprobe')) {
    $expectedTool = [IO.Path]::GetFullPath((Join-Path $target "_internal\bin\$tool.exe"))
    if (-not $expectedTool.Equals($runtime.$tool, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Frozen sidecar discovered an external media tool: $tool"
    }
}
Write-Output $runtimeOutput
