[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:18001",
    [string]$RepositoryId = "",
    [switch]$VerifyRuntime,
    [switch]$AllowOffline,
    [switch]$RequireProduction,
    [switch]$RequireGitHubPublish,
    [string]$Output = ".run/acceptance/platform-preflight.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

$RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepositoryRoot
$localApiKeyFile = Join-Path $RepositoryRoot ".run/local-full/api-key"
if ([string]::IsNullOrWhiteSpace($env:RESEARCHFORGE_API_KEY) -and (Test-Path -LiteralPath $localApiKeyFile -PathType Leaf)) {
    $localApiKey = (Get-Content -LiteralPath $localApiKeyFile -Raw).Trim()
    if ($localApiKey) { $env:RESEARCHFORGE_API_KEY = $localApiKey }
}
$arguments = @(
    (Join-Path $RepositoryRoot "backend/scripts/run_platform_preflight.py"),
    "--base-url", $BaseUrl,
    "--output", $Output,
    "--quiet"
)
if ($RepositoryId) { $arguments += @("--repository-id", $RepositoryId) }
if ($VerifyRuntime) { $arguments += "--verify-runtime" }
if ($AllowOffline) { $arguments += "--allow-offline" }
if ($RequireProduction) { $arguments += "--require-production" }
if ($RequireGitHubPublish) { $arguments += "--require-github-publish" }

$env:PYTHONPATH = Join-Path $RepositoryRoot "backend"
& python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Platform preflight did not meet the requested requirements. Read $Output."
}
Write-Host "Platform preflight passed. Report: $Output"
