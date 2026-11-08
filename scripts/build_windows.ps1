$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$repo = Split-Path -Parent $PSScriptRoot
$desktop = Join-Path $repo 'apps\desktop'
$bundleDir = Join-Path $desktop 'src-tauri\target\release\bundle\nsis'
$savedRustFlags = $env:CARGO_ENCODED_RUSTFLAGS
$separator = [string][char]31
$flags = if ($savedRustFlags) { @($savedRustFlags) } elseif ($env:RUSTFLAGS) { $env:RUSTFLAGS.Trim() -split '\s+' } else { @() }
$flags = @($flags | Where-Object { $_ }) + @(
    "--remap-path-prefix=$repo=C:\coverlab\source",
    "--remap-path-prefix=$env:USERPROFILE=C:\coverlab\toolchain"
)
$env:CARGO_ENCODED_RUSTFLAGS = $flags -join $separator

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Assert-GeneratedPath([string]$Candidate) {
    $targetRoot = [IO.Path]::GetFullPath((Join-Path $desktop 'src-tauri\target')).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath($Candidate)
    if (-not $full.StartsWith($targetRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe generated path: $full"
    }
}

Push-Location $repo
try {
    uv sync --all-packages --python 3.12 --locked
    Assert-NativeSuccess 'uv sync'

    uv run --locked --python 3.12 pytest -q
    Assert-NativeSuccess 'pytest'

    uv run --locked --python 3.12 ruff format --check packages services tests
    Assert-NativeSuccess 'ruff format'

    uv run --locked --python 3.12 ruff check packages services tests
    Assert-NativeSuccess 'ruff check'

    uv run --locked --python 3.12 mypy packages/engine-core/src services/engine/src
    Assert-NativeSuccess 'mypy'

    & "$PSScriptRoot\build_sidecar.ps1" -Clean

    Assert-GeneratedPath $bundleDir
    if (Test-Path -LiteralPath $bundleDir) {
        [IO.Directory]::Delete([IO.Path]::GetFullPath($bundleDir), $true)
    }

    Push-Location $desktop
    try {
        npm ci
        Assert-NativeSuccess 'npm ci'

        npm test -- --run
        Assert-NativeSuccess 'npm test'

        npm run build
        Assert-NativeSuccess 'npm run build'

        cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
        Assert-NativeSuccess 'cargo fmt'

        cargo test --manifest-path src-tauri/Cargo.toml
        Assert-NativeSuccess 'cargo test'

        cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
        Assert-NativeSuccess 'cargo clippy'

        npm run tauri build -- --bundles nsis
        Assert-NativeSuccess 'Tauri NSIS build'
    } finally {
        Pop-Location
    }

    & "$PSScriptRoot\verify_windows_bundle.ps1"
} finally {
    Pop-Location
    $env:CARGO_ENCODED_RUSTFLAGS = $savedRustFlags
}
