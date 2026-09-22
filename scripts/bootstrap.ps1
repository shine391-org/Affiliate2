param(
    [switch]$ForceNpmInstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppRoot = Join-Path $RepoRoot "app"
$VenvRoot = Join-Path $RepoRoot ".venv"
$PythonExe = Join-Path $VenvRoot "Scripts\python.exe"
$FrontendRoot = Join-Path $AppRoot "frontend"

if (-not (Test-Path $AppRoot)) {
    throw "Canonical app directory not found: $AppRoot"
}

$systemPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $systemPython -and -not (Test-Path $PythonExe)) {
    throw "Python 3.11+ is required and was not found on PATH."
}

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating Python virtual environment at $VenvRoot"
    & python -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed" }
}

$version = & $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
$majorMinor = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$majorMinor -lt [version]"3.11") {
    throw "Python 3.11+ required; .venv is using $version"
}
Write-Host "Python $version"

Write-Host "Installing backend dependencies..."
& $PythonExe -m pip install -q --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $PythonExe -m pip install -r (Join-Path $AppRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "backend dependency install failed" }

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js/npm 18+ is required and npm was not found on PATH."
}
Write-Host "Node $(& node --version)"

Push-Location $FrontendRoot
try {
    if ($ForceNpmInstall -or -not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
        if (Test-Path (Join-Path $FrontendRoot "package-lock.json")) {
            & npm ci
        } else {
            & npm install
        }
        if ($LASTEXITCODE -ne 0) { throw "frontend dependency install failed" }
    } else {
        Write-Host "Frontend node_modules already present; skipping npm install."
    }
}
finally {
    Pop-Location
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "FFmpeg is required and was not found on PATH."
}
Write-Host "FFmpeg found."

if (-not (Get-Command magick -ErrorAction SilentlyContinue) -and
    -not (Get-Command convert -ErrorAction SilentlyContinue)) {
    Write-Warning "ImageMagick was not found. Some media features may be unavailable."
} else {
    Write-Host "ImageMagick found."
}

$RootEnv = Join-Path $RepoRoot ".env"
$ExampleEnv = Join-Path $AppRoot ".env.example"
if (-not (Test-Path $RootEnv) -and (Test-Path $ExampleEnv)) {
    Copy-Item $ExampleEnv $RootEnv
    Write-Host "Created root .env from app/.env.example. Fill provider keys locally or use Settings."
}

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Development: .\scripts\dev.ps1"
Write-Host "Stable run:  .\scripts\run.ps1"
