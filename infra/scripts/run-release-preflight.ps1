[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipFrontend,
    [switch]$RequireProduction,
    [string]$Output = ".run/acceptance/release-preflight.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepositoryRoot
$arguments = @(
    (Join-Path $RepositoryRoot "backend/scripts/release_preflight.py"),
    "--output", $Output
)
if ($SkipTests) { $arguments += "--skip-tests" }
if ($SkipFrontend) { $arguments += "--skip-frontend" }
if ($RequireProduction) { $arguments += "--require-production" }
& python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release preflight failed. Read $Output for the failed checks."
}
Write-Host "Release preflight passed. Report: $Output"
