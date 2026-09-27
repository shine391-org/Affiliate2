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

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating Python virtual environment at $VenvRoot"
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $py311 = (& py -0p 2>$null | Select-String -Pattern "^-V:3\.11|^\s+-V:3\.11")
        if ($py311) {
            & py -3.11 -m venv $VenvRoot
            $created = ($LASTEXITCODE -eq 0)
        }
    }
    if (-not $created -and (Get-Command python -ErrorAction SilentlyContinue)) {
        & python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            & python -m venv $VenvRoot
            $created = ($LASTEXITCODE -eq 0)
        }
    }
    if (-not $created -and (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "Using uv to provision Python 3.11."
        & uv venv --seed --python 3.11 $VenvRoot
        $created = ($LASTEXITCODE -eq 0)
    }
    if (-not $created -or -not (Test-Path $PythonExe)) {
        throw "Python 3.11.x is required. Install it from python.org, or install uv and rerun setup.ps1."
    }
}

$version = & $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
$majorMinor = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($majorMinor -ne "3.11") {
    throw "Python 3.11.x required by the V1 dependency lock; .venv is using $version"
}
Write-Host "Python $version"

Write-Host "Installing backend dependencies..."
& $PythonExe -m pip install -q --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
$RequirementsFile = Join-Path $AppRoot "requirements-lock.txt"
if (-not (Test-Path $RequirementsFile)) {
    $RequirementsFile = Join-Path $AppRoot "requirements.txt"
}
& $PythonExe -m pip install -r $RequirementsFile
if ($LASTEXITCODE -ne 0) { throw "backend dependency install failed" }

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js 22+/npm 10+ are required and npm was not found on PATH."
}
$nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
$npmMajor = [int]((& npm --version).Split('.')[0])
if ($nodeMajor -lt 22) { throw "Node.js 22+ required; detected $(& node --version)" }
if ($npmMajor -lt 10) { throw "npm 10+ required; detected $(& npm --version)" }
Write-Host "Node $(& node --version); npm $(& npm --version)"

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
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "ffprobe is required and was not found on PATH."
}
Write-Host "FFmpeg and ffprobe found."

if (-not (Get-Command magick -ErrorAction SilentlyContinue) -and
    -not (Get-Command convert -ErrorAction SilentlyContinue)) {
    Write-Warning "ImageMagick was not found. Some media features may be unavailable."
} else {
    Write-Host "ImageMagick found."
}

$RootEnv = Join-Path $RepoRoot ".env"
$ExampleEnv = Join-Path $RepoRoot ".env.example"
if (-not (Test-Path $ExampleEnv)) { $ExampleEnv = Join-Path $AppRoot ".env.example" }
if (-not (Test-Path $RootEnv) -and (Test-Path $ExampleEnv)) {
    Copy-Item $ExampleEnv $RootEnv
    Write-Host "Created root .env from app/.env.example. Fill provider keys locally or use Settings."
}

Write-Host "Running release pre-flight..."
& $PythonExe (Join-Path $PSScriptRoot "preflight.py") --root $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "Affiliate2 pre-flight failed" }

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Development: .\scripts\dev.ps1"
Write-Host "Stable run:  .\scripts\run.ps1"
