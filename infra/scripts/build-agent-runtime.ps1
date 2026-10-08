[CmdletBinding()]
param(
    [ValidateSet("mini-swe-agent")]
    [string]$Runtime = "mini-swe-agent",
    [string]$Tag = "researchforge-agent-runtime:mini-swe-agent",
    [string]$MiniSweAgentVersion = "2.2.7",
    [switch]$Push,
    [string]$Registry
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required to build the agent runtime image."
}

if ($Push -and [string]::IsNullOrWhiteSpace($Registry)) {
    throw "-Push requires -Registry, for example registry.example.com/researchforge."
}

$imageTag = if ($Push) { "$Registry/$Tag" } else { $Tag }
Push-Location $RepositoryRoot
try {
    & docker build `
        --file backend/Dockerfile.agent-runtime `
        --build-arg "MINI_SWE_AGENT_VERSION=$MiniSweAgentVersion" `
        --tag $imageTag `
        .
    if ($LASTEXITCODE -ne 0) { throw "Agent runtime image build failed." }

    if ($Push) {
        & docker push $imageTag
        if ($LASTEXITCODE -ne 0) { throw "Agent runtime image push failed." }
    }
}
finally {
    Pop-Location
}

Write-Host "Agent runtime image ready: $imageTag"
