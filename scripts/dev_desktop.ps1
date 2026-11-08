$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

uv sync --all-packages --python 3.12 --locked
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed with exit code $LASTEXITCODE"
}

& "$PSScriptRoot\build_sidecar.ps1" -Clean

Push-Location "$repo\apps\desktop"
try {
    npm ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed with exit code $LASTEXITCODE"
    }
    npm run tauri dev
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri development process failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
