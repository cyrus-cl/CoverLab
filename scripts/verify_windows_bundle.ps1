param(
    [string]$BundleDirectory
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$repo = Split-Path -Parent $PSScriptRoot
$desktop = Join-Path $repo 'apps\desktop'
$tauriRoot = Join-Path $desktop 'src-tauri'
if (-not $BundleDirectory) {
    $BundleDirectory = Join-Path $tauriRoot 'target\release\bundle\nsis'
}

$bundleRoot = [IO.Path]::GetFullPath($BundleDirectory)
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $tauriRoot 'target\release\bundle')).TrimEnd('\') + '\'
if (-not $bundleRoot.StartsWith($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Bundle directory is outside the generated release tree: $bundleRoot"
}
if (-not (Test-Path -LiteralPath $bundleRoot -PathType Container)) {
    throw "NSIS bundle directory is missing: $bundleRoot"
}

$installers = @(Get-ChildItem -LiteralPath $bundleRoot -Filter '*-setup.exe' -File)
if ($installers.Count -ne 1) {
    throw "Expected exactly one *-setup.exe, found $($installers.Count)"
}
$installer = $installers[0]
if ($installer.Length -lt 1MB) {
    throw "Installer is unexpectedly small: $($installer.Length) bytes"
}

$sourceEngine = Join-Path $tauriRoot 'resources\engine'
$sourceExecutable = Join-Path $sourceEngine 'ai4music-engine.exe'
$sourceRuntime = Join-Path $sourceEngine '_internal'
$sourceNotices = Join-Path $repo 'docs\licenses\THIRD_PARTY_NOTICES.md'
foreach ($required in @($sourceExecutable, $sourceRuntime, $sourceNotices)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required release resource is missing: $required"
    }
}

function Get-TreeManifest([string]$Root) {
    $fullRoot = [IO.Path]::GetFullPath($Root)
    @(
        Get-ChildItem -LiteralPath $fullRoot -Recurse -File -Force |
            ForEach-Object {
                [pscustomobject]@{
                    path = [IO.Path]::GetRelativePath($fullRoot, $_.FullName).Replace('\', '/')
                    bytes = $_.Length
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                }
            } |
            Sort-Object path
    ) | ConvertTo-Json -Compress
}

$sevenZip = Get-Command '7z' -ErrorAction SilentlyContinue
$sevenZipPath = if ($sevenZip) { $sevenZip.Source } else { $null }
if (-not $sevenZipPath) {
    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles '7-Zip\7z.exe'
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe'
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $sevenZipPath = $candidate
            break
        }
    }
}
if (-not $sevenZipPath) {
    throw 'The full 7-Zip CLI (7z.exe) is required to inspect the NSIS payload without installing it'
}

$inspectionRoot = Join-Path $repo ".runtime\bundle-inspection-$PID-$([Guid]::NewGuid().ToString('N'))"
$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $repo '.runtime')).TrimEnd('\') + '\'
$inspectionFull = [IO.Path]::GetFullPath($inspectionRoot)
if (-not $inspectionFull.StartsWith($runtimeRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe inspection path: $inspectionFull"
}

New-Item -ItemType Directory -Force -Path $inspectionFull | Out-Null
try {
    & $sevenZipPath x $installer.FullName "-o$inspectionFull" -y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "7z could not extract the NSIS payload (exit $LASTEXITCODE)"
    }

    $files = @(Get-ChildItem -LiteralPath $inspectionFull -Recurse -File -Force)
    $payloadEngine = Join-Path $inspectionFull 'engine'
    $payloadExecutable = Join-Path $payloadEngine 'ai4music-engine.exe'
    $payloadRuntime = Join-Path $payloadEngine '_internal'
    $payloadNotices = Join-Path $inspectionFull 'THIRD_PARTY_NOTICES.md'
    $payloadRequirements = @($payloadExecutable, $payloadRuntime, $payloadNotices)
    foreach ($required in $payloadRequirements) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Installer payload is missing the required release path: $required"
        }
    }

    $sourceNoticeHash = (Get-FileHash -LiteralPath $sourceNotices -Algorithm SHA256).Hash
    $payloadNoticeHash = (Get-FileHash -LiteralPath $payloadNotices -Algorithm SHA256).Hash
    if (-not $sourceNoticeHash.Equals($payloadNoticeHash, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Payload notice hash does not match source'
    }

    if ((Get-TreeManifest $sourceEngine) -cne (Get-TreeManifest $payloadEngine)) {
        throw 'Engine payload manifest does not match source'
    }

    $forbiddenFiles = @(
        $files | Where-Object {
            $_.Name -in @('.env', 'app.db', 'secrets.json', 'token.txt') -or
            $_.Extension.ToLowerInvariant() -in @(
                '.sqlite', '.sqlite3', '.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wma'
            ) -or
            $_.FullName -match '[\\/]fixtures?[\\/]'
        }
    )
    if ($forbiddenFiles.Count -gt 0) {
        $relative = $forbiddenFiles | ForEach-Object {
            [IO.Path]::GetRelativePath($inspectionFull, $_.FullName)
        }
        throw "Sensitive or test files found in installer payload: $($relative -join ', ')"
    }

    $developerPaths = @(
        [IO.Path]::GetFullPath($repo),
        ([IO.Path]::GetPathRoot($repo) + 'ai4music')
    ) | Select-Object -Unique
    foreach ($file in $files) {
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        $latin = [Text.Encoding]::Latin1.GetString($bytes)
        $unicode = [Text.Encoding]::Unicode.GetString($bytes)
        foreach ($developerPath in $developerPaths) {
            if (
                $latin.Contains($developerPath, [StringComparison]::OrdinalIgnoreCase) -or
                $unicode.Contains($developerPath, [StringComparison]::OrdinalIgnoreCase)
            ) {
                $relative = [IO.Path]::GetRelativePath($inspectionFull, $file.FullName)
                throw "Developer absolute path found in installer payload: $relative"
            }
        }
    }

    $hash = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    [pscustomobject]@{
        installer = $installer.FullName
        bytes = $installer.Length
        sha256 = $hash
        inspectedFiles = $files.Count
        engineRuntimeFiles = @(Get-ChildItem -LiteralPath $sourceEngine -Recurse -File).Count
    } | ConvertTo-Json -Compress
} finally {
    if (Test-Path -LiteralPath $inspectionFull) {
        [IO.Directory]::Delete($inspectionFull, $true)
    }
}
