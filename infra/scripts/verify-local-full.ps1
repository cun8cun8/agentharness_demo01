[CmdletBinding()]
param(
    [string]$ProjectName = "researchforge-local",
    [int]$ApiPort = 18001,
    [int]$FrontendPort = 13010,
    [switch]$VerifyModel,
    [string]$ModelName = "qwen-plus"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$apiKeyFile = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) ".run/local-full/api-key"
$portsFile = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) ".run/local-full/ports.json"
if (Test-Path -LiteralPath $portsFile -PathType Leaf) {
    $ports = Get-Content -LiteralPath $portsFile -Raw | ConvertFrom-Json
    if ($ApiPort -eq 18001 -and $ports.api_port) { $ApiPort = [int]$ports.api_port }
    if ($FrontendPort -eq 13010 -and $ports.frontend_port) { $FrontendPort = [int]$ports.frontend_port }
}
$script:ApiHeaders = @{}
if (Test-Path -LiteralPath $apiKeyFile -PathType Leaf) {
    $apiKey = (Get-Content -LiteralPath $apiKeyFile -Raw).Trim()
    if ($apiKey) { $script:ApiHeaders = @{ "X-API-Key" = $apiKey } }
}

$checks = [System.Collections.Generic.List[object]]::new()

function Add-Check([string]$Name, [bool]$Passed, [string]$Evidence) {
    $checks.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        evidence = $Evidence
    })
}

function Get-Json([string]$Uri) {
    Invoke-RestMethod -Uri $Uri -Headers $script:ApiHeaders -TimeoutSec 30 -ErrorAction Stop
}

try {
    $health = Get-Json "http://127.0.0.1:$ApiPort/health"
    Add-Check "API 健康" ($health.status -eq "ok") ("status=" + [string]$health.status)
} catch {
    Add-Check "API 健康" $false $_.Exception.Message
}

try {
    $ready = Get-Json "http://127.0.0.1:$ApiPort/health/ready"
    Add-Check "API 就绪" ($ready.status -eq "ready") ("status=" + [string]$ready.status)
} catch {
    Add-Check "API 就绪" $false $_.Exception.Message
}

try {
    $adapters = Get-Json "http://127.0.0.1:$ApiPort/api/v1/adapters"
    $count = @($adapters.items).Count
    Add-Check "Runtime 适配器" ($count -ge 3) ("$count 个适配器")
} catch {
    Add-Check "Runtime 适配器" $false $_.Exception.Message
}

try {
    $sandbox = Get-Json "http://127.0.0.1:$ApiPort/api/v1/sandbox/check"
    $sandboxEvidence = if ($sandbox.message) { [string]$sandbox.message } else { [string]$sandbox.configured_backend }
    Add-Check "沙箱探针" ([bool]$sandbox.ready) $sandboxEvidence
} catch {
    Add-Check "沙箱探针" $false $_.Exception.Message
}

try {
    $frontend = Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/tasks" -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
    Add-Check "工作台页面" ($frontend.StatusCode -eq 200) ("HTTP " + [string]$frontend.StatusCode)
} catch {
    Add-Check "工作台页面" $false $_.Exception.Message
}

foreach ($suffix in @("api-1", "worker-1", "frontend-1")) {
    $container = "$ProjectName-$suffix"
    try {
        $status = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $container 2>$null).Trim()
        Add-Check "容器 $suffix" ($status -in @("healthy", "running")) $status
    } catch {
        Add-Check "容器 $suffix" $false "容器不存在或 Docker 不可用"
    }
}

if ($VerifyModel) {
    try {
        $models = Get-Json "http://127.0.0.1:$ApiPort/api/v1/models/health?role=coding&status=active&verify_connectivity=true&limit=100"
        $model = @($models.items) | Where-Object { $_.model_name -eq $ModelName } | Select-Object -First 1
        Add-Check "模型 $ModelName" ($null -ne $model -and [bool]$model.healthy) $(if ($null -eq $model) { "未登记" } else { [string]$model.reason })
    } catch {
        Add-Check "模型 $ModelName" $false $_.Exception.Message
    }
}

$passed = @($checks | Where-Object passed).Count
$result = [pscustomobject]@{
    status = if ($passed -eq $checks.Count) { "ready" } else { "not_ready" }
    passed = $passed
    total = $checks.Count
    checks = $checks
}
$result | ConvertTo-Json -Depth 6
if ($result.status -ne "ready") { exit 1 }
