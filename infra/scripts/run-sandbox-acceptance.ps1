[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:18001",
    [string]$ApiKey = "",
    [ValidateSet("", "docker", "kubernetes")]
    [string]$RequireBackend = "",
    [string]$Output = ".run/acceptance/sandbox-latest.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepositoryRoot

$arguments = @(
    (Join-Path $RepositoryRoot "backend/scripts/sandbox_acceptance.py"),
    "--base-url", $BaseUrl,
    "--output", $Output
)
if ($ApiKey) { $arguments += @("--api-key", $ApiKey) }
if ($RequireBackend) { $arguments += @("--require-backend", $RequireBackend) }

$env:PYTHONPATH = Join-Path $RepositoryRoot "backend"
& python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Sandbox acceptance failed. Read $Output."
}
Write-Host "Sandbox acceptance passed. Report: $Output"
