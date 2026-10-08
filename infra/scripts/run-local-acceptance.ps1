[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$EnableNetwork,
    [switch]$NoStart,
    [string]$ModelName = "qwen-plus",
    [ValidateRange(1, 10)]
    [int]$Limit = 10,
    [int]$JobTimeoutSeconds = 3600,
    [int]$AcceptanceRetries = 1,
    [string]$ResumeJobId,
    [string]$RepositoryId,
    [string]$RepositoryGoal = "修复仓库缺陷，不修改测试文件，并让测试全部通过。",
    [string]$RepositoryTestCommand = "pytest -q",
    [switch]$Publish,
    [switch]$Push,
    [switch]$CreatePullRequest,
    [switch]$SkipGolden,
    [switch]$WithGolden,
    [switch]$RequireIsolatedSandbox,
    [string]$Output = ".run/acceptance/complete-latest.json",
    [int]$Neo4jHttpPort = 17475,
    [int]$Neo4jBoltPort = 17688
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepositoryRoot

if ($CreatePullRequest -and -not $Push) {
    throw "-CreatePullRequest requires -Push."
}
if (($Publish -or $Push -or $CreatePullRequest) -and [string]::IsNullOrWhiteSpace($RepositoryId)) {
    throw "Publication flags require -RepositoryId."
}

if (-not $NoStart) {
    $startParameters = @{
        Neo4jHttpPort = $Neo4jHttpPort
        Neo4jBoltPort = $Neo4jBoltPort
    }
    if ($Build) { $startParameters.Build = $true }
    if ($EnableNetwork) {
        $startParameters.EnableNetwork = $true
        $startParameters.ForceRecreate = $true
    }
    & (Join-Path $RepositoryRoot "infra/scripts/start-local-full.ps1") @startParameters
    if ($LASTEXITCODE -ne 0) { throw "Local full stack failed to start." }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required to run the acceptance workflow."
}

$arguments = @(
    (Join-Path $RepositoryRoot "backend/scripts/run_complete_acceptance.py"),
    "--base-url", "http://127.0.0.1:18001",
    "--model-name", $ModelName,
    "--limit", $Limit,
    "--job-timeout-seconds", $JobTimeoutSeconds,
    "--acceptance-retries", $AcceptanceRetries,
    "--output", $Output
)
if ($RepositoryId) { $arguments += @("--repository-id", $RepositoryId, "--repository-goal", $RepositoryGoal, "--repository-test-command", $RepositoryTestCommand) }
if ($Publish) { $arguments += "--publish" }
if ($Push) { $arguments += "--push" }
if ($CreatePullRequest) { $arguments += "--create-pull-request" }
if ($SkipGolden) { $arguments += "--skip-golden" }
if ($WithGolden) { $arguments += "--with-golden" }
if ($RequireIsolatedSandbox) { $arguments += "--require-isolated-sandbox" }
if ($ResumeJobId) { $arguments += @("--resume-job-id", $ResumeJobId) }

$env:PYTHONPATH = Join-Path $RepositoryRoot "backend"
& python @arguments
if ($LASTEXITCODE -ne 0) {
    Write-Error "Complete acceptance failed. Read $Output and the matching .md report."
    exit $LASTEXITCODE
}
Write-Host "Complete acceptance passed. Report: $Output"
