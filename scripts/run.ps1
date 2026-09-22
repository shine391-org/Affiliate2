param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppRoot = Join-Path $RepoRoot "app"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$FrontendRoot = Join-Path $AppRoot "frontend"
$RuntimeRoot = Join-Path $RepoRoot ".runtime"
$PidFile = Join-Path $RuntimeRoot "processes.json"

if (-not (Test-Path $PythonExe) -or -not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
}

& (Join-Path $PSScriptRoot "stop.ps1") -Quiet

Write-Host "Building frontend..."
Push-Location $FrontendRoot
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

$env:VIRALMINT_DATA_DIR = $RepoRoot
$env:CONTENT_ENGINE_ROOT = $RepoRoot
$env:VIRALMINT_OVERLAY = "viralmint_overlay"
$env:VIRALMINT_NO_BROWSER = "1"
$env:DEBUG = "false"
$env:PYTHONPATH = "$RepoRoot;$AppRoot" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { "" })

$backendCmd = "& '$PythonExe' -m uvicorn backend.main:app --host 127.0.0.1 --port 16888"
$backend = Start-Process powershell.exe -ArgumentList @(
    "-NoExit", "-NoProfile", "-Command", $backendCmd
) -WorkingDirectory $AppRoot -PassThru

@{
    mode = "stable"
    backend_shell_pid = $backend.Id
    started_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Encoding UTF8 $PidFile

$ready = $false
for ($i = 0; $i -lt 80; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:16888/docs" -TimeoutSec 2
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
            $ready = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}

if ($ready) {
    Start-Process "http://127.0.0.1:16888"
    Write-Host "Affiliate2 stable UI: http://127.0.0.1:16888"
} else {
    Write-Warning "Backend did not become reachable within 40 seconds. Check the backend console window."
}
