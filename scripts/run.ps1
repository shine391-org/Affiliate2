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

& $PythonExe (Join-Path $PSScriptRoot "preflight.py") --root $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "Affiliate2 pre-flight failed" }

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

$HostAddress = "127.0.0.1"
$Port = 16888
$DataRoot = $RepoRoot
$ContentRoot = $RepoRoot
$EnvFile = Join-Path $RepoRoot ".env"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*HOST\s*=\s*(.+?)\s*$') { $HostAddress = $Matches[1].Trim('"', "'") }
        if ($line -match '^\s*PORT\s*=\s*(\d+)\s*$') { $Port = [int]$Matches[1] }
        if ($line -match '^\s*VIRALMINT_DATA_DIR\s*=\s*(.+?)\s*$') { $DataRoot = $Matches[1].Trim('"', "'") }
        if ($line -match '^\s*CONTENT_ENGINE_ROOT\s*=\s*(.+?)\s*$') { $ContentRoot = $Matches[1].Trim('"', "'") }
    }
}

$env:VIRALMINT_DATA_DIR = $DataRoot
$env:CONTENT_ENGINE_ROOT = $ContentRoot
$env:VIRALMINT_NO_BROWSER = "1"
$env:DEBUG = "false"
$env:PYTHONPATH = "$RepoRoot;$AppRoot" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" } else { "" })

$backendCmd = "& '$PythonExe' -m uvicorn backend.main:app --host '$HostAddress' --port $Port"
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
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($r.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}

if ($ready) {
    Start-Process "http://127.0.0.1:$Port"
    Write-Host "Affiliate2 stable UI: http://127.0.0.1:$Port"
} else {
    Write-Warning "Backend did not become reachable within 40 seconds. Check the backend console window."
}
