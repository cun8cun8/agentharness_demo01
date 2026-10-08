[CmdletBinding()]
param(
    [string]$ProjectName = "researchforge-local",
    [int]$ApiPort = 18001,
    [int]$FrontendPort = 13010,
    [ValidateRange(1, 16)][int]$WorkerReplicas = 2,
    [int]$PostgresPort = 15433,
    [int]$RedisPort = 16380,
    [int]$MinioApiPort = 19020,
    [int]$MinioConsolePort = 19021,
    [int]$RedpandaPort = 29092,
    [int]$PrometheusPort = 19091,
    [int]$AlertmanagerPort = 19093,
    [int]$GrafanaPort = 13001,
    [int]$Neo4jHttpPort = 17474,
    [int]$Neo4jBoltPort = 17687,
    [string]$GitHubAppId = "",
    [string]$GitHubAppPrivateKeyFile = "",
    [switch]$UseOllama,
    [string]$OllamaModel = "qwen2.5:7b",
    [int]$OllamaPort = 11434,
    [ValidateRange(30, 7200)][int]$OllamaModelTimeoutSeconds = 180,
    [switch]$UseDockerSandbox,
    [switch]$EnableNetwork,
    [switch]$Production,
    [switch]$Build,
    [switch]$ForceRecreate,
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:ReservedPorts = @{}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$RepositoryRoot = Split-Path -Parent $RepositoryRoot
$InfrastructureDirectory = Join-Path $RepositoryRoot "infra"
$RunDirectory = Join-Path $RepositoryRoot ".run/local-full"
$ComposeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.full.yml")

function New-LocalSecret([string]$Path) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [Convert]::ToBase64String($bytes) | Set-Content -LiteralPath $Path -Encoding ascii -NoNewline
}

function Invoke-Compose([string[]]$Arguments) {
    Push-Location $InfrastructureDirectory
    try {
        & docker compose --project-name $ProjectName @ComposeFiles @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Docker Compose command failed." }
    }
    finally { Pop-Location }
}

$apiKeyPath = Join-Path $RunDirectory "api-key"
$metricsTokenPath = Join-Path $RunDirectory "metrics-token"
$githubAppIdPath = Join-Path $RunDirectory "github-app-id"

function Set-LocalFullEnvironment {
    $env:RESEARCHFORGE_API_KEY_FILE = $apiKeyPath
    $env:RESEARCHFORGE_METRICS_TOKEN_FILE = $metricsTokenPath
    $env:RESEARCHFORGE_API_HOST_PORT = $ApiPort
    $env:RESEARCHFORGE_FRONTEND_HOST_PORT = $FrontendPort
    $env:RESEARCHFORGE_FRONTEND_PUBLIC_URL = "http://127.0.0.1:$FrontendPort/"
    $env:RESEARCHFORGE_POSTGRES_HOST_PORT = $PostgresPort
    $env:RESEARCHFORGE_REDIS_HOST_PORT = $RedisPort
    $env:RESEARCHFORGE_MINIO_API_HOST_PORT = $MinioApiPort
    $env:RESEARCHFORGE_MINIO_CONSOLE_HOST_PORT = $MinioConsolePort
    $env:RESEARCHFORGE_REDPANDA_EXTERNAL_PORT = $RedpandaPort
    $env:RESEARCHFORGE_PROMETHEUS_HOST_PORT = $PrometheusPort
    $env:RESEARCHFORGE_ALERTMANAGER_HOST_PORT = $AlertmanagerPort
    $env:RESEARCHFORGE_GRAFANA_PORT = $GrafanaPort
    $env:RESEARCHFORGE_NEO4J_HTTP_PORT = $Neo4jHttpPort
    $env:RESEARCHFORGE_NEO4J_BOLT_PORT = $Neo4jBoltPort

    # The App ID is public metadata, but persisting it prevents local restarts
    # from silently dropping GitHub App authentication. Private keys and
    # webhook secrets remain external secret inputs and are never written here.
    $resolvedGitHubAppId = $GitHubAppId.Trim()
    if ([string]::IsNullOrWhiteSpace($resolvedGitHubAppId) -and -not [string]::IsNullOrWhiteSpace($env:RESEARCHFORGE_GITHUB_APP_ID)) {
        $resolvedGitHubAppId = $env:RESEARCHFORGE_GITHUB_APP_ID.Trim()
    }
    if ([string]::IsNullOrWhiteSpace($resolvedGitHubAppId) -and (Test-Path -LiteralPath $githubAppIdPath)) {
        $resolvedGitHubAppId = (Get-Content -Raw -LiteralPath $githubAppIdPath).Trim()
    }
    if (-not [string]::IsNullOrWhiteSpace($resolvedGitHubAppId)) {
        if ($resolvedGitHubAppId -notmatch '^[0-9]+$') {
            throw "-GitHubAppId must contain only digits."
        }
        $env:RESEARCHFORGE_GITHUB_APP_ID = $resolvedGitHubAppId
        Set-Content -LiteralPath $githubAppIdPath -Value $resolvedGitHubAppId -Encoding ascii -NoNewline
    }

    $resolvedPrivateKeyPath = $GitHubAppPrivateKeyFile.Trim()
    if ([string]::IsNullOrWhiteSpace($resolvedPrivateKeyPath) -and -not [string]::IsNullOrWhiteSpace($env:RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY_FILE)) {
        $resolvedPrivateKeyPath = $env:RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY_FILE.Trim()
    }
    if (-not [string]::IsNullOrWhiteSpace($resolvedPrivateKeyPath)) {
        try {
            $resolvedPrivateKeyPath = [System.IO.Path]::GetFullPath($resolvedPrivateKeyPath)
        }
        catch {
            throw "GitHub App private key path is invalid."
        }
        if (-not (Test-Path -LiteralPath $resolvedPrivateKeyPath -PathType Leaf)) {
            throw "GitHub App private key file does not exist: $resolvedPrivateKeyPath"
        }
        $firstLine = Get-Content -LiteralPath $resolvedPrivateKeyPath -TotalCount 1
        if ($firstLine -notmatch '^-----BEGIN (?:RSA )?PRIVATE KEY-----$') {
            throw "GitHub App private key file is not a PEM private key."
        }
        $env:RESEARCHFORGE_GITHUB_APP_PRIVATE_KEY_FILE = $resolvedPrivateKeyPath
    }

    # The worker itself is containerized; Golden Tasks run in its local workspace.
    # Production remains the Compose default and requires Docker/Kubernetes sandbox isolation.
    if ([string]::IsNullOrWhiteSpace($env:RESEARCHFORGE_EVENT_BUS_BACKEND)) {
        $env:RESEARCHFORGE_EVENT_BUS_BACKEND = "redis"
    }
    if ([string]::IsNullOrWhiteSpace($env:RESEARCHFORGE_JOB_VISIBILITY_TIMEOUT_SECONDS)) {
        $env:RESEARCHFORGE_JOB_VISIBILITY_TIMEOUT_SECONDS = "120"
    }
    $env:RESEARCHFORGE_CORS_ORIGINS = "http://127.0.0.1:$FrontendPort,http://localhost:$FrontendPort"
    $env:RESEARCHFORGE_ENV = if ($Production) { "production" } else { "container" }
    $env:RESEARCHFORGE_AUTH_MODE = if ($Production) { "production" } else { "development" }
    # Keep the mounted local API key usable in development as well as
    # production. Explicit X-API-Key clients (acceptance and automation)
    # must authenticate against the same secret file in every local mode.
    $env:RESEARCHFORGE_API_KEY_ENV = "RESEARCHFORGE_API_KEY_SECRET"
    # The local workbench uses one API process so JSON/cache-backed development
    # state cannot diverge between Uvicorn workers. Production uses PostgreSQL
    # and can scale API replicas independently.
    $env:RESEARCHFORGE_API_WORKERS = "1"
    $env:RESEARCHFORGE_ALLOW_MOCK_MODELS = if ($Production) { "0" } else { "1" }
    $env:RESEARCHFORGE_SANDBOX_NETWORK_ENABLED = "0"
    $env:RESEARCHFORGE_SANDBOX_IMAGE = "researchforge-sandbox:latest"
    if ($UseDockerSandbox) {
        $env:RESEARCHFORGE_SANDBOX_BACKEND = "docker"
        $env:RESEARCHFORGE_SANDBOX_SHARED_WORKSPACE_ROOT = "/var/lib/researchforge/workspaces"
        $env:RESEARCHFORGE_SANDBOX_WORKSPACE_VOLUME = "$ProjectName`_workspace-data"
    }
    else {
        $env:RESEARCHFORGE_SANDBOX_BACKEND = "local"
        Remove-Item Env:RESEARCHFORGE_SANDBOX_SHARED_WORKSPACE_ROOT -ErrorAction SilentlyContinue
        Remove-Item Env:RESEARCHFORGE_SANDBOX_WORKSPACE_VOLUME -ErrorAction SilentlyContinue
    }
    # Prefer the mature open-source runtime policy locally as well. Set
    # RESEARCHFORGE_AGENT_BACKEND=native explicitly for offline fixture tests.
    if ([string]::IsNullOrWhiteSpace($env:RESEARCHFORGE_AGENT_BACKEND)) {
        $env:RESEARCHFORGE_AGENT_BACKEND = "auto"
    }
    if ($EnableNetwork) {
        $env:RESEARCHFORGE_NETWORK_ENABLED = "1"
    }
    elseif ([string]::IsNullOrWhiteSpace($env:RESEARCHFORGE_NETWORK_ENABLED)) {
        $env:RESEARCHFORGE_NETWORK_ENABLED = "0"
    }

    if ($UseOllama) {
        if ([string]::IsNullOrWhiteSpace($OllamaModel)) { throw "-OllamaModel must not be empty." }
        if ($OllamaPort -lt 1 -or $OllamaPort -gt 65535) { throw "-OllamaPort must be between 1 and 65535." }
        # Docker Desktop exposes the host through host.docker.internal. Ollama
        # accepts any non-empty OpenAI-compatible key; this marker is local-only.
        $env:RESEARCHFORGE_NETWORK_ENABLED = "1"
        $env:RESEARCHFORGE_MODEL_PROVIDER = "openai_compatible"
        $env:RESEARCHFORGE_MODEL_BASE_URL = "http://host.docker.internal:$OllamaPort/v1"
        $env:RESEARCHFORGE_MODEL_NAME = $OllamaModel.Trim()
        $env:RESEARCHFORGE_RESEARCH_MODEL_NAME = $OllamaModel.Trim()
        $env:RESEARCHFORGE_MODEL_TIMEOUT_SECONDS = [string]$OllamaModelTimeoutSeconds
        $env:OPENAI_API_KEY = "local-ollama"
    }
}

function Test-ProjectOwnsPort([int]$Port) {
    $rows = & docker ps -a --format '{{.Names}}|{{.Ports}}' 2>$null
    foreach ($row in $rows) {
        $parts = $row -split "\|", 2
        if ($parts.Count -eq 2 -and $parts[0] -like "$ProjectName-*" -and $parts[1] -match "(?:127\.0\.0\.1|0\.0\.0\.0|\[::\]):${Port}->") {
            return $true
        }
    }
    return $false
}

function Test-LocalPortAvailable([int]$Port) {
    if (Test-ProjectOwnsPort $Port) { return $true }
    $rows = & docker ps -a --format '{{.Names}}|{{.Ports}}' 2>$null
    foreach ($row in $rows) {
        $parts = $row -split "\|", 2
        if ($parts.Count -eq 2 -and $parts[1] -match "(?:127\.0\.0\.1|0\.0\.0\.0|\[::\]):${Port}->") {
            return $false
        }
    }
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Resolve-FreePort([int]$Requested, [string]$Name) {
    for ($candidate = $Requested; $candidate -lt ($Requested + 200); $candidate++) {
        if (-not $script:ReservedPorts.ContainsKey($candidate) -and (Test-LocalPortAvailable $candidate)) {
            if ($candidate -ne $Requested) {
                Write-Warning "$Name port $Requested is busy; using $candidate."
            }
            $script:ReservedPorts[$candidate] = $Name
            return $candidate
        }
    }
    throw "Unable to find an available port for $Name near $Requested."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required to start or stop the local full stack."
}

if (-not $Stop) {
    $ApiPort = Resolve-FreePort $ApiPort "API"
    $FrontendPort = Resolve-FreePort $FrontendPort "frontend"
    $PostgresPort = Resolve-FreePort $PostgresPort "PostgreSQL"
    $RedisPort = Resolve-FreePort $RedisPort "Redis"
    $MinioApiPort = Resolve-FreePort $MinioApiPort "MinIO API"
    $MinioConsolePort = Resolve-FreePort $MinioConsolePort "MinIO console"
    $RedpandaPort = Resolve-FreePort $RedpandaPort "Redpanda"
    $PrometheusPort = Resolve-FreePort $PrometheusPort "Prometheus"
    $AlertmanagerPort = Resolve-FreePort $AlertmanagerPort "Alertmanager"
    $GrafanaPort = Resolve-FreePort $GrafanaPort "Grafana"
    $Neo4jHttpPort = Resolve-FreePort $Neo4jHttpPort "Neo4j HTTP"
    $Neo4jBoltPort = Resolve-FreePort $Neo4jBoltPort "Neo4j Bolt"
}

New-Item -ItemType Directory -Force -Path $RunDirectory | Out-Null
Set-LocalFullEnvironment

if (-not $Stop) {
    [ordered]@{
        api_port = $ApiPort
        frontend_port = $FrontendPort
        postgres_port = $PostgresPort
        redis_port = $RedisPort
        minio_api_port = $MinioApiPort
        minio_console_port = $MinioConsolePort
        redpanda_port = $RedpandaPort
        prometheus_port = $PrometheusPort
        alertmanager_port = $AlertmanagerPort
        grafana_port = $GrafanaPort
        neo4j_http_port = $Neo4jHttpPort
        neo4j_bolt_port = $Neo4jBoltPort
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RunDirectory "ports.json") -Encoding utf8
}

if ($Stop) {
    Invoke-Compose @("down", "--remove-orphans")
    Write-Host "Local full stack '$ProjectName' stopped. Persistent named volumes were retained."
    exit 0
}

if ($EnableNetwork -and -not $UseOllama -and [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY) -and [string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)) {
    throw "-EnableNetwork requires OPENAI_API_KEY or DASHSCOPE_API_KEY in the current PowerShell session. The value is never printed."
}

if ($UseOllama) {
    try {
        $ollamaTags = Invoke-RestMethod -Uri "http://127.0.0.1:$OllamaPort/api/tags" -TimeoutSec 10
        $available = @($ollamaTags.models | ForEach-Object { [string]$_.name })
        if ($available -notcontains $OllamaModel) { throw "model '$OllamaModel' is not installed" }
    }
    catch {
        throw "-UseOllama requires a reachable Ollama model '$OllamaModel' on http://127.0.0.1:$OllamaPort. $($_.Exception.Message)"
    }
}

if ($UseDockerSandbox) {
    & docker image inspect $env:RESEARCHFORGE_SANDBOX_IMAGE *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "-UseDockerSandbox requires Docker image '$($env:RESEARCHFORGE_SANDBOX_IMAGE)'. Build it before starting the stack."
    }
}

if (-not (Test-Path -LiteralPath $apiKeyPath)) { New-LocalSecret $apiKeyPath }
if (-not (Test-Path -LiteralPath $metricsTokenPath)) { New-LocalSecret $metricsTokenPath }

Invoke-Compose @("config", "--quiet")
$upArguments = @("up", "--detach", "--wait", "--wait-timeout", "240", "--scale", "worker=$WorkerReplicas")
if ($Build) { $upArguments += "--build" }
if ($ForceRecreate) { $upArguments += "--force-recreate" }
Invoke-Compose $upArguments

$deadline = (Get-Date).AddSeconds(60)
$health = $null
do {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health" -TimeoutSec 5
        if ($health.status -eq "ok") { break }
    }
    catch { Start-Sleep -Seconds 2 }
} while ((Get-Date) -lt $deadline)

if ($null -eq $health -or $health.status -ne "ok") {
    throw "API health endpoint did not become ready. Run '.\\infra\\scripts\\start-local-full.ps1 -Stop' after collecting docker compose logs."
}

try {
    $frontend = Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/tasks" -UseBasicParsing -TimeoutSec 10
}
catch {
    throw "Frontend did not become reachable: $($_.Exception.Message)"
}

if ($frontend.StatusCode -ne 200) {
    throw "Frontend returned HTTP $($frontend.StatusCode)."
}

Write-Host "ResearchForge local full stack is ready."
Write-Host "Model network: $env:RESEARCHFORGE_NETWORK_ENABLED"
if ($UseOllama) { Write-Host "Model gateway: Ollama $OllamaModel (host.docker.internal:$OllamaPort)" }
if ($UseDockerSandbox) { Write-Host "Sandbox: Docker isolated execution ($($env:RESEARCHFORGE_SANDBOX_IMAGE))" }
Write-Host "Workbench: http://127.0.0.1:$FrontendPort/tasks"
Write-Host "API:       http://127.0.0.1:$ApiPort/health"
Write-Host "Grafana:   http://127.0.0.1:$GrafanaPort"
Write-Host "MinIO:     http://127.0.0.1:$MinioConsolePort"
