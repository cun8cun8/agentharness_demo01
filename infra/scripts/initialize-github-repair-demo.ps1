[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$RemoteUrl,
    [string]$Branch = "main",
    [string]$Destination = ".run/github-repair-demo",
    [string]$TokenEnvironmentVariable = "RESEARCHFORGE_GITHUB_TOKEN"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Git([string[]]$Arguments) {
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($Arguments -join ' ')"
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to initialize the GitHub repair demo."
}
if (-not $RemoteUrl.StartsWith("https://github.com/", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "RemoteUrl must use an HTTPS GitHub URL."
}

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$templatePath = Join-Path $repositoryRoot "examples/github-repair-demo"
$destinationPath = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $Destination))
if (-not (Test-Path -LiteralPath $templatePath)) {
    throw "Demo template not found: $templatePath"
}
if (Test-Path -LiteralPath $destinationPath) {
    throw "Destination already exists: $destinationPath. Choose a new -Destination."
}

$token = [Environment]::GetEnvironmentVariable($TokenEnvironmentVariable, "Process")
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "Set $TokenEnvironmentVariable in this PowerShell session before running the script."
}

$encodedCredentials = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes("x-access-token:$token")
)
$existingCount = 0
$existingCountValue = [Environment]::GetEnvironmentVariable("GIT_CONFIG_COUNT", "Process")
if ($existingCountValue) {
    [void][int]::TryParse($existingCountValue, [ref]$existingCount)
}
$previousEnvironment = @{}
foreach ($name in @("GIT_CONFIG_COUNT")) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
for ($index = 0; $index -lt $existingCount; $index++) {
    foreach ($name in @("GIT_CONFIG_KEY_$index", "GIT_CONFIG_VALUE_$index")) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
}
foreach ($name in @("GIT_CONFIG_KEY_$existingCount", "GIT_CONFIG_VALUE_$existingCount")) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    [Environment]::SetEnvironmentVariable("GIT_CONFIG_COUNT", [string]($existingCount + 1), "Process")
    [Environment]::SetEnvironmentVariable("GIT_CONFIG_KEY_$existingCount", "http.extraHeader", "Process")
    [Environment]::SetEnvironmentVariable("GIT_CONFIG_VALUE_$existingCount", "Authorization: Basic $encodedCredentials", "Process")

    New-Item -ItemType Directory -Path $destinationPath | Out-Null
    Copy-Item -Path (Join-Path $templatePath "*") -Destination $destinationPath -Recurse
    Push-Location $destinationPath
    try {
        Invoke-Git @("init", "--initial-branch", $Branch)
        Invoke-Git @("config", "user.name", "ResearchForge Demo")
        Invoke-Git @("config", "user.email", "researchforge-demo@local.invalid")
        Invoke-Git @("add", ".")
        Invoke-Git @("commit", "-m", "chore: add intentionally failing pricing demo")
        Invoke-Git @("remote", "add", "origin", $RemoteUrl)
        Invoke-Git @("push", "--set-upstream", "origin", $Branch)
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($entry in $previousEnvironment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}

Write-Host "GitHub repair demo initialized: $destinationPath"
Write-Host "Next: click '同步仓库' in ResearchForge, then run an automated repair."
