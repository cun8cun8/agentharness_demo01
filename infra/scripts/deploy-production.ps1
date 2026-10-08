[CmdletBinding()]
param(
    [ValidateSet("preflight", "bootstrap-state", "apply-infrastructure", "configure-kubeconfig", "deploy-production", "verify", "all")]
    [string]$Phase = "preflight",
    [switch]$Execute,
    [string]$StateValues = "infra/terraform/state-backend/terraform.tfvars",
    [string]$InfrastructureValues = "infra/terraform/aws/terraform.tfvars",
    [string]$BackendConfig = "infra/terraform/aws/backend.hcl",
    [string]$Namespace = "researchforge",
    [string]$BaseUrl,
    [string]$ApiKeyEnv = "RESEARCHFORGE_API_KEY"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$RepositoryRoot = Split-Path -Parent $RepositoryRoot
$StateDirectory = Join-Path $RepositoryRoot "infra/terraform/state-backend"
$InfrastructureDirectory = Join-Path $RepositoryRoot "infra/terraform/aws"
$BackendDirectory = Join-Path $RepositoryRoot "backend"

function Resolve-RepositoryPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $RepositoryRoot $Path
}

function Invoke-Terraform([string]$Directory, [string[]]$Arguments) {
    $terraform = Get-Command terraform -ErrorAction SilentlyContinue
    if ($null -ne $terraform) {
        Push-Location $Directory
        try {
            & $terraform.Source @Arguments
            if ($LASTEXITCODE -ne 0) { throw "terraform $($Arguments -join ' ') failed." }
        }
        finally { Pop-Location }
        return
    }

    $relative = [System.IO.Path]::GetRelativePath($RepositoryRoot, $Directory).Replace("\\", "/")
    & docker run --rm -v "${RepositoryRoot}:/workspace" -w "/workspace/$relative" hashicorp/terraform:1.14.6 @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Terraform Docker fallback failed." }
}

function Get-TerraformOutput([string]$Directory, [string[]]$Arguments) {
    $terraform = Get-Command terraform -ErrorAction SilentlyContinue
    if ($null -ne $terraform) {
        Push-Location $Directory
        try {
            $output = & $terraform.Source @Arguments
            if ($LASTEXITCODE -ne 0) { throw "terraform $($Arguments -join ' ') failed." }
            return ($output -join [Environment]::NewLine).Trim()
        }
        finally { Pop-Location }
    }

    $relative = [System.IO.Path]::GetRelativePath($RepositoryRoot, $Directory).Replace("\\", "/")
    $output = & docker run --rm -v "${RepositoryRoot}:/workspace" -w "/workspace/$relative" hashicorp/terraform:1.14.6 @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Terraform Docker fallback failed." }
    return ($output -join [Environment]::NewLine).Trim()
}

function Convert-ToTerraformPath([string]$Directory, [string]$Path) {
    return [System.IO.Path]::GetRelativePath($Directory, $Path).Replace("\\", "/")
}

function Invoke-Aws([string[]]$Arguments) {
    $aws = Get-Command aws -ErrorAction SilentlyContinue
    if ($null -eq $aws) {
        throw "AWS CLI is not installed. Install AWS CLI v2 and authenticate with aws sso login or AWS credentials before deployment."
    }
    & $aws.Source @Arguments
    if ($LASTEXITCODE -ne 0) { throw "aws $($Arguments -join ' ') failed." }
}

function Require-File([string]$Path, [string]$Purpose) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Purpose is missing: $Path"
    }
}

function Assert-NoPlaceholders([string]$Path, [string]$Purpose) {
    $content = (Get-Content -LiteralPath $Path | Where-Object { $_ -notmatch '^\s*#' }) -join [Environment]::NewLine
    if ($content -match 'REPLACE_WITH|example\.invalid|203\.0\.113\.10') {
        throw "$Purpose still contains deployment placeholders: $Path"
    }
}

function Get-ProjectPython {
    $candidates = @(
        (Join-Path $BackendDirectory ".venv312\Scripts\python.exe"),
        (Join-Path $BackendDirectory ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) { return $python.Source }
    throw "Python 3 is required for ResearchForge deployment checks."
}

function Get-InfrastructureOutput([string]$Name) {
    $value = Get-TerraformOutput $InfrastructureDirectory @("output", "-raw", $Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Terraform output '$Name' is unavailable. Apply infrastructure first."
    }
    return $value
}

function Assert-CloudAccess {
    Invoke-Aws @("sts", "get-caller-identity", "--output", "json") | Out-Null
}

function Invoke-Preflight {
    $stateValuesPath = Resolve-RepositoryPath $StateValues
    $infrastructureValuesPath = Resolve-RepositoryPath $InfrastructureValues
    Require-File $stateValuesPath "Terraform state values"
    Require-File $infrastructureValuesPath "Terraform infrastructure values"
    Assert-NoPlaceholders $stateValuesPath "Terraform state values"
    Assert-NoPlaceholders $infrastructureValuesPath "Terraform infrastructure values"
    Assert-CloudAccess
    Invoke-Terraform $StateDirectory @("fmt", "-check")
    Invoke-Terraform $InfrastructureDirectory @("fmt", "-check")
    Write-Host "Production deployment preflight passed."
}

function Initialize-StateBackend {
    $stateValuesPath = Resolve-RepositoryPath $StateValues
    Require-File $stateValuesPath "Terraform state values"
    Assert-NoPlaceholders $stateValuesPath "Terraform state values"
    $stateValuesArgument = Convert-ToTerraformPath $StateDirectory $stateValuesPath
    Invoke-Terraform $StateDirectory @("init", "-input=false")
    Invoke-Terraform $StateDirectory @("plan", "-input=false", "-var-file=$stateValuesArgument", "-out=state-backend.tfplan")
    if (-not $Execute) {
        Write-Host "State backend plan created. Re-run with -Execute to apply it."
        return
    }
    Invoke-Terraform $StateDirectory @("apply", "-input=false", "state-backend.tfplan")
    $backendPath = Resolve-RepositoryPath $BackendConfig
    Get-TerraformOutput $StateDirectory @("output", "-raw", "backend_hcl") | Set-Content -LiteralPath $backendPath -Encoding ascii
    Write-Host "State backend applied and backend.hcl generated."
}

function Apply-Infrastructure {
    $valuesPath = Resolve-RepositoryPath $InfrastructureValues
    $backendPath = Resolve-RepositoryPath $BackendConfig
    Require-File $valuesPath "Terraform infrastructure values"
    Require-File $backendPath "Terraform backend configuration"
    Assert-NoPlaceholders $valuesPath "Terraform infrastructure values"
    Assert-NoPlaceholders $backendPath "Terraform backend configuration"
    $valuesArgument = Convert-ToTerraformPath $InfrastructureDirectory $valuesPath
    $backendArgument = Convert-ToTerraformPath $InfrastructureDirectory $backendPath
    Invoke-Terraform $InfrastructureDirectory @("init", "-reconfigure", "-input=false", "-backend-config=$backendArgument")
    Invoke-Terraform $InfrastructureDirectory @("plan", "-input=false", "-var-file=$valuesArgument", "-out=researchforge.tfplan")
    if (-not $Execute) {
        Write-Host "Infrastructure plan created. Re-run with -Execute to apply it."
        return
    }
    Invoke-Terraform $InfrastructureDirectory @("apply", "-input=false", "researchforge.tfplan")
    Write-Host "Infrastructure applied."
}

function Configure-Kubeconfig {
    $clusterName = Get-InfrastructureOutput "cluster_name"
    $valuesPath = Resolve-RepositoryPath $InfrastructureValues
    $region = (Select-String -LiteralPath $valuesPath -Pattern '^region\s*=\s*"([^"]+)"').Matches.Groups[1].Value
    if ([string]::IsNullOrWhiteSpace($region)) { throw "region is missing from $valuesPath" }
    Invoke-Aws @("eks", "update-kubeconfig", "--region", $region, "--name", $clusterName)
    $account = [string](Invoke-Aws @("sts", "get-caller-identity", "--query", "Account", "--output", "text"))
    kubectl config use-context "arn:aws:eks:${region}:$($account.Trim()):cluster/$clusterName"
    if ($LASTEXITCODE -ne 0) { throw "Unable to select EKS kubeconfig context." }
}

function Deploy-Production {
    $preflight = Join-Path $BackendDirectory "scripts/production_preflight.py"
    $python = Get-ProjectPython
    & $python $preflight "--kustomize" (Join-Path $RepositoryRoot "infra/kubernetes/production")
    if ($LASTEXITCODE -ne 0) {
        throw "Production configuration preflight failed. Resolve all reported placeholders and unsafe defaults before deployment."
    }
    $rendered = & kubectl kustomize (Join-Path $RepositoryRoot "infra/kubernetes/production")
    if ($LASTEXITCODE -ne 0) { throw "Unable to render production Kustomize overlay." }
    if ($rendered -match 'REPLACE_WITH|example\.invalid') {
        throw "Production Kustomize overlay still contains placeholders. Set production images, DNS, S3, model, EFS, and certificate values before deployment."
    }
    if (-not $Execute) {
        Write-Host "Production Kustomize overlay rendered. Re-run with -Execute to apply it."
        return
    }
    $rendered | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed." }
    & $python "$BackendDirectory\scripts\cluster_preflight.py" "--namespace" $Namespace
    if ($LASTEXITCODE -ne 0) { throw "Cluster preflight failed." }
}

function Verify-Production {
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        throw "-BaseUrl is required for the verify phase."
    }
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($ApiKeyEnv))) {
        throw "Environment variable $ApiKeyEnv is required for the verify phase."
    }
    $python = Get-ProjectPython
    & $python "$BackendDirectory\scripts\acceptance_probe.py" "--base-url" $BaseUrl "--production"
    if ($LASTEXITCODE -ne 0) { throw "Production acceptance probe failed." }
    & $python "$BackendDirectory\scripts\load_probe.py" "--base-url" $BaseUrl "--path" "/api/v1/system/readiness" "--requests" "100" "--concurrency" "10" "--max-p95-ms" "1000"
    if ($LASTEXITCODE -ne 0) { throw "Production load probe failed." }
}

switch ($Phase) {
    "preflight" { Invoke-Preflight }
    "bootstrap-state" { Invoke-Preflight; Initialize-StateBackend }
    "apply-infrastructure" { Invoke-Preflight; Apply-Infrastructure }
    "configure-kubeconfig" { Assert-CloudAccess; Configure-Kubeconfig }
    "deploy-production" { Deploy-Production }
    "verify" { Verify-Production }
    "all" {
        Invoke-Preflight
        Initialize-StateBackend
        if ($Execute) {
            Apply-Infrastructure
            Configure-Kubeconfig
            Deploy-Production
            Verify-Production
        }
    }
}
