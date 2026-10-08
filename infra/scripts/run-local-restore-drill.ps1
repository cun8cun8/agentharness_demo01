param(
    [string]$OutputDirectory = '.run/acceptance/local-restore-drill',
    [string]$PostgresDsn = 'postgresql://researchforge:researchforge@127.0.0.1:15433/researchforge',
    [string]$ArtifactEndpoint = 'http://127.0.0.1:19020'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $projectRoot
$backupRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
if (-not $backupRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup directory must stay inside the project workspace.'
}
$backupRoot = Join-Path $backupRoot ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$pending = @(docker exec researchforge-local-redis-1 redis-cli XPENDING researchforge:jobs:stream researchforge-workers)
if ($LASTEXITCODE -ne 0 -or $pending[0] -ne '0') { throw 'Wait until queued work has finished before the consistent backup drill.' }
$groupInfo = @(docker exec researchforge-local-redis-1 redis-cli XINFO GROUPS researchforge:jobs:stream)
if ($LASTEXITCODE -ne 0) { throw 'Failed to inspect queue consumer groups.' }
for ($index = 0; $index -lt $groupInfo.Count - 1; $index++) {
    if ($groupInfo[$index] -eq 'lag' -and $groupInfo[$index + 1] -ne '0') {
        throw 'Unconsumed queue messages remain; wait before the restore drill.'
    }
}
$stopped = $false
try {
    $stopped = $true
    docker stop --timeout 30 researchforge-local-api-1 researchforge-local-worker-1
    if ($LASTEXITCODE -ne 0) { throw 'Failed to quiesce API and Worker.' }
    $env:RESEARCHFORGE_POSTGRES_DSN = $PostgresDsn
    python backend/scripts/restore_drill.py --output-dir (Join-Path $backupRoot 'database') --tools-container researchforge-local-postgres-1
    if ($LASTEXITCODE -ne 0) { throw 'Database restore drill failed.' }
    if (-not $env:MINIO_ROOT_USER) { $env:MINIO_ROOT_USER = 'researchforge' }
    if (-not $env:MINIO_ROOT_PASSWORD) { $env:MINIO_ROOT_PASSWORD = 'researchforge' }
    python backend/scripts/artifact_restore_drill.py --endpoint $ArtifactEndpoint --output-dir (Join-Path $backupRoot 'artifacts')
    if ($LASTEXITCODE -ne 0) { throw 'Artifact restore drill failed.' }
    $workspaceOutput = Join-Path $backupRoot 'workspace'
    New-Item -ItemType Directory -Path $workspaceOutput | Out-Null
    docker run --rm --network none --entrypoint python --mount 'type=volume,source=researchforge-local_workspace-data,target=/source,readonly' --mount "type=bind,source=$workspaceOutput,target=/backup" --mount "type=bind,source=$projectRoot/backend/scripts/workspace_restore_drill.py,target=/drill.py,readonly" researchforge-local-api:latest /drill.py --source /source --output-dir /backup
    if ($LASTEXITCODE -ne 0) { throw 'Workspace restore drill failed.' }
    $configOutput = Join-Path $backupRoot 'configuration'
    New-Item -ItemType Directory -Path $configOutput | Out-Null
    foreach ($relative in @('infra/docker-compose.yml','infra/docker-compose.full.yml','infra/scripts/start-local-full.ps1')) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $relative) -Destination $configOutput
    }
    Get-ChildItem -LiteralPath $configOutput -File | Get-FileHash -Algorithm SHA256 | Select-Object Path,Hash | ConvertTo-Json | Set-Content (Join-Path $backupRoot 'configuration-hashes.json') -Encoding utf8
    'Local restore drill passed. Secrets remain in the original credential files and are not copied into this backup.' | Set-Content (Join-Path $backupRoot 'result.txt') -Encoding utf8
} finally {
    if ($stopped) {
        docker start researchforge-local-api-1 researchforge-local-worker-1
        if ($LASTEXITCODE -ne 0) { Write-Error 'Service restart failed; inspect the named local containers.' }
    }
}
Write-Output "Backup and isolated restore verified: $backupRoot"
