param(
    [switch]$StableBackend
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppRoot = Join-Path $RepoRoot "app"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RuntimeRoot = Join-Path $RepoRoot ".runtime"
$PidFile = Join-Path $RuntimeRoot "processes.json"

if (-not (Test-Path $PythonExe) -or -not (Test-Path (Join-Path $AppRoot "frontend\node_modules"))) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
}

& (Join-Path $PSScriptRoot "stop.ps1") -Quiet

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

$env:VIRALMINT_DATA_DIR = $RepoRoot
$env:CONTENT_ENGINE_ROOT = $RepoRoot
$env:VIRALMINT_NO_BROWSER = "1"
$env:PYTHONPATH = "$RepoRoot;$AppRoot" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { "" })

$reloadArg = if ($StableBackend) { "" } else { " --reload" }
$backendCmd = "& '$PythonExe' -m uvicorn backend.main:app --host 127.0.0.1 --port 16888$reloadArg"
$frontendCmd = "npm run dev -- --host 127.0.0.1 --port 5173"

$backend = Start-Process powershell.exe -ArgumentList @(
    "-NoExit", "-NoProfile", "-Command", $backendCmd
) -WorkingDirectory $AppRoot -PassThru

$frontend = Start-Process powershell.exe -ArgumentList @(
    "-NoExit", "-NoProfile", "-Command", $frontendCmd
) -WorkingDirectory (Join-Path $AppRoot "frontend") -PassThru

@{
    mode = $(if ($StableBackend) { "dev-stable-backend" } else { "dev" })
    backend_shell_pid = $backend.Id
    frontend_shell_pid = $frontend.Id
    started_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Encoding UTF8 $PidFile

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5173" -TimeoutSec 2
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
            $ready = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}

if ($ready) {
    Start-Process "http://127.0.0.1:5173"
    Write-Host "Affiliate2 dev UI: http://127.0.0.1:5173"
    Write-Host "Backend API:       http://127.0.0.1:16888"
} else {
    Write-Warning "Vite did not become reachable within 30 seconds. Check the frontend/backend console windows."
}
