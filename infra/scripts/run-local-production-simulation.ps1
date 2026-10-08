[CmdletBinding()]
param(
    [string]$ProjectName = "researchforge-local",
    [string]$ModelName = "qwen2.5:7b",
    [ValidateRange(30, 7200)][int]$ModelTimeoutSeconds = 180,
    [string]$GitHubAppId = "",
    [string]$GitHubAppPrivateKeyFile = "",
    [switch]$Build,
    [switch]$ForceRecreate,
    [string]$Output = ".run/acceptance/local-production-simulation.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$startParams = @{
    ProjectName = $ProjectName
    UseOllama = $true
    UseDockerSandbox = $true
    OllamaModel = $ModelName
    OllamaModelTimeoutSeconds = $ModelTimeoutSeconds
    Production = $true
}
if ($GitHubAppId) { $startParams.GitHubAppId = $GitHubAppId }
if ($GitHubAppPrivateKeyFile) { $startParams.GitHubAppPrivateKeyFile = $GitHubAppPrivateKeyFile }
if ($Build) { $startParams.Build = $true }
if ($ForceRecreate) { $startParams.ForceRecreate = $true }
& (Join-Path $PSScriptRoot "start-local-full.ps1") @startParams

$verifyOutput = & (Join-Path $PSScriptRoot "verify-local-full.ps1") -ProjectName $ProjectName -VerifyModel -ModelName $ModelName | Out-String
$preflightOutput = & (Join-Path $PSScriptRoot "run-platform-preflight.ps1") -Output $Output | Out-String
$portsFile = Join-Path $root ".run/local-full/ports.json"
$ports = if (Test-Path -LiteralPath $portsFile -PathType Leaf) { Get-Content -LiteralPath $portsFile -Raw | ConvertFrom-Json } else { $null }
$apiPort = if ($ports -and $ports.api_port) { [int]$ports.api_port } elseif ($env:RESEARCHFORGE_API_HOST_PORT) { $env:RESEARCHFORGE_API_HOST_PORT } else { "18001" }
$keyFile = Join-Path $root ".run/local-full/api-key"
$headers = @{}
if (Test-Path -LiteralPath $keyFile -PathType Leaf) {
    $key = (Get-Content -LiteralPath $keyFile -Raw).Trim()
    if ($key) { $headers["X-API-Key"] = $key }
}
$production = Invoke-RestMethod -Uri "http://127.0.0.1:$apiPort/api/v1/system/production-readiness?verify_dependencies=true" -Headers $headers -TimeoutSec 30
$report = [ordered]@{
    schema_version = "researchforge-local-production-simulation.v1"
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    project_name = $ProjectName
    model = $ModelName
    local_checks = $verifyOutput.Trim()
    platform_preflight = $preflightOutput.Trim()
    production_readiness = $production
    external_requirements = @(
        "公网 HTTPS Webhook 和固定域名仍未配置",
        "当前沙箱为本机 Docker Compose 隔离执行，不代表 Kubernetes 租户隔离",
        "当前 Ollama 是本机模型，不代表云模型 SLA、限流或计费验收"
    )
}
$OutputPath = Join-Path $root $Output
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Host "Local production simulation completed. Report: $Output"
Write-Host "Production readiness is intentionally reported separately; inspect the report before any real cutover."
