param([switch]$ForceNpmInstall)
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "scripts\bootstrap.ps1") -ForceNpmInstall:$ForceNpmInstall
