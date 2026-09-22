param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PidFile = Join-Path $RepoRoot ".runtime\processes.json"

if (-not (Test-Path $PidFile)) {
    if (-not $Quiet) { Write-Host "No managed Affiliate2 processes recorded." }
    exit 0
}

try {
    $state = Get-Content $PidFile -Raw | ConvertFrom-Json
} catch {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    if (-not $Quiet) { Write-Warning "Invalid process state file removed." }
    exit 0
}

$pids = @()
if ($state.backend_shell_pid) { $pids += [int]$state.backend_shell_pid }
if ($state.frontend_shell_pid) { $pids += [int]$state.frontend_shell_pid }

foreach ($pidValue in ($pids | Select-Object -Unique)) {
    $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($proc) {
        & taskkill.exe /PID $pidValue /T /F 2>$null | Out-Null
        if (-not $Quiet) { Write-Host "Stopped managed process tree PID $pidValue" }
    }
}

Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
if (-not $Quiet) { Write-Host "Affiliate2 managed processes stopped." }
